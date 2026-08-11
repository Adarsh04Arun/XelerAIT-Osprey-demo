"""The 'hospital system' -- an in-memory stand-in for a PAS/EHR scheduling API.

Everything here is the system of record the agent is trying to change. It is
intentionally dumb: it enforces almost nothing itself, because the whole point
of the exercise is that the guardrails live in Osprey. The only checks kept
here are the ones a real API would enforce at the storage layer regardless
(e.g. a slot can't hold two appointments) -- and even those exist mainly so a
guardrail bug shows up as a caught exception rather than corrupt state.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from .config import settings


# ---------------------------------------------------------------------------
# Entities
# ---------------------------------------------------------------------------


@dataclass
class Provider:
    id: str
    name: str
    department: str
    daily_cap: int = 8
    # Dates (YYYY-MM-DD) the provider is not working, on top of weekends.
    unavailable_dates: List[str] = field(default_factory=list)


@dataclass
class Patient:
    id: str
    full_name: str
    date_of_birth: str
    phone: str
    email: str = ''
    address: str = ''
    insurance_id: str = ''
    preferred_language: str = 'en'
    emergency_contact: str = ''
    consent_given: bool = True
    notes: str = ''


@dataclass
class Appointment:
    id: str
    patient_id: str
    provider_id: str
    start: str  # "YYYY-MM-DDTHH:MM"
    department: str
    status: str = 'booked'  # booked | cancelled


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class HospitalStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.providers: Dict[str, Provider] = {}
        self.patients: Dict[str, Patient] = {}
        self.appointments: Dict[str, Appointment] = {}
        self._counter = 0
        self.seed()

    # -- seeding ------------------------------------------------------------

    def seed(self) -> None:
        """Reset to a known fixture. Called on startup and by the test suite."""
        with self._lock:
            self.providers.clear()
            self.patients.clear()
            self.appointments.clear()
            self._counter = 0

            today = datetime.now().date()
            # Put one provider on leave tomorrow-ish so the 'provider
            # unavailable' guardrail has something real to catch.
            on_leave = (today + timedelta(days=1)).isoformat()

            for p in [
                Provider('dr-okafor', 'Dr. Amara Okafor', 'general-practice', daily_cap=8),
                Provider('dr-lindqvist', 'Dr. Sven Lindqvist', 'dermatology', daily_cap=6),
                Provider('dr-rao', 'Dr. Meera Rao', 'cardiology', daily_cap=4,
                         unavailable_dates=[on_leave]),
                # Deliberately tiny cap -- makes the capacity guardrail easy to
                # trip in a demo without booking eight appointments first.
                Provider('dr-haddad', 'Dr. Yusuf Haddad', 'physiotherapy', daily_cap=2),
            ]:
                self.providers[p.id] = p

            for pt in [
                Patient('pat-1001', 'Rosa Iyer', '1984-03-12', '+44 7700 900101',
                        email='rosa.iyer@example.com', insurance_id='INS-44821'),
                Patient('pat-1002', 'Daniel Whitfield', '1971-11-02', '+44 7700 900102',
                        email='d.whitfield@example.com', insurance_id='INS-77310'),
                Patient('pat-1003', 'Grace Mbeki', '1995-06-28', '+44 7700 900103',
                        email='g.mbeki@example.com', insurance_id='INS-20194'),
            ]:
                self.patients[pt.id] = pt

            # An existing appointment belonging to pat-1002. The cross-patient
            # access guardrail needs a real appointment owned by someone else.
            next_open = self.next_open_day(today + timedelta(days=2))
            self._put_appointment(
                Appointment(
                    id='apt-5001',
                    patient_id='pat-1002',
                    provider_id='dr-okafor',
                    start=f'{next_open.isoformat()}T10:00',
                    department='general-practice',
                )
            )

    def _put_appointment(self, apt: Appointment) -> None:
        self.appointments[apt.id] = apt

    def _next_id(self, prefix: str) -> str:
        self._counter += 1
        return f'{prefix}-{6000 + self._counter}'

    # -- schedule helpers ---------------------------------------------------

    @staticmethod
    def next_open_day(start):
        d = start
        while d.weekday() not in settings.open_weekdays:
            d += timedelta(days=1)
        return d

    def is_within_clinic_hours(self, when: datetime) -> bool:
        if when.weekday() not in settings.open_weekdays:
            return False
        if when.hour < settings.clinic_open_hour:
            return False
        # A slot starting exactly at closing time runs past close.
        if when.hour >= settings.clinic_close_hour:
            return False
        return True

    def is_valid_slot_start(self, when: datetime) -> bool:
        """Slots sit on a fixed grid. Catches invented times like 09:07."""
        return when.minute % settings.slot_minutes == 0 and when.second == 0

    def slot_occupant(self, provider_id: str, start: str) -> Optional[str]:
        with self._lock:
            for apt in self.appointments.values():
                if (
                    apt.provider_id == provider_id
                    and apt.start == start
                    and apt.status == 'booked'
                ):
                    return apt.patient_id
        return None

    def provider_booked_on(self, provider_id: str, date_str: str) -> int:
        with self._lock:
            return sum(
                1
                for a in self.appointments.values()
                if a.provider_id == provider_id
                and a.status == 'booked'
                and a.start.startswith(date_str)
            )

    def provider_available_on(self, provider: Provider, when: datetime) -> bool:
        if when.weekday() not in settings.open_weekdays:
            return False
        return when.date().isoformat() not in provider.unavailable_dates

    # -- lookups ------------------------------------------------------------

    def find_patient_by_identity(self, full_name: str, date_of_birth: str) -> Optional[Patient]:
        """Name + DOB match, the usual duplicate-record heuristic."""
        needle = (full_name or '').strip().lower()
        with self._lock:
            for pt in self.patients.values():
                if pt.full_name.strip().lower() == needle and pt.date_of_birth == date_of_birth:
                    return pt
        return None

    def appointments_for(self, patient_id: str) -> List[Appointment]:
        with self._lock:
            return sorted(
                (a for a in self.appointments.values()
                 if a.patient_id == patient_id and a.status == 'booked'),
                key=lambda a: a.start,
            )

    # -- mutations (only reached after Osprey allows the action) ------------

    def book(self, patient_id: str, provider_id: str, start: str, department: str) -> Appointment:
        with self._lock:
            occupant = self.slot_occupant(provider_id, start)
            if occupant is not None:
                # Reaching this means a guardrail failed to catch a double
                # booking. Loud failure beats silent corruption.
                raise ValueError(f'slot {start} for {provider_id} already held by {occupant}')
            apt = Appointment(
                id=self._next_id('apt'),
                patient_id=patient_id,
                provider_id=provider_id,
                start=start,
                department=department,
            )
            self._put_appointment(apt)
            return apt

    def reschedule(self, appointment_id: str, start: str) -> Appointment:
        with self._lock:
            apt = self.appointments[appointment_id]
            occupant = self.slot_occupant(apt.provider_id, start)
            if occupant is not None and occupant != apt.patient_id:
                raise ValueError(f'slot {start} already held by {occupant}')
            apt.start = start
            return apt

    def cancel(self, appointment_id: str) -> Appointment:
        with self._lock:
            apt = self.appointments[appointment_id]
            apt.status = 'cancelled'
            return apt

    def create_patient(self, **kwargs) -> Patient:
        with self._lock:
            pt = Patient(id=self._next_id('pat'), **kwargs)
            self.patients[pt.id] = pt
            return pt

    def update_patient(self, patient_id: str, field_name: str, value: str) -> Patient:
        with self._lock:
            pt = self.patients[patient_id]
            if not hasattr(pt, field_name):
                raise ValueError(f'unknown field {field_name}')
            setattr(pt, field_name, value)
            return pt

    # -- presentation -------------------------------------------------------

    def provider_summary(self) -> List[dict]:
        with self._lock:
            return [asdict(p) for p in self.providers.values()]

    def open_slots(self, provider_id: str, date_str: str, limit: int = 6) -> List[str]:
        """Free slot starts on a given date. This is what the agent should be
        offering patients instead of inventing times."""
        provider = self.providers.get(provider_id)
        if provider is None:
            return []
        try:
            day = datetime.fromisoformat(f'{date_str}T00:00')
        except ValueError:
            return []
        if not self.provider_available_on(provider, day):
            return []

        out: List[str] = []
        cursor = day.replace(hour=settings.clinic_open_hour, minute=0)
        end = day.replace(hour=settings.clinic_close_hour, minute=0)
        now = datetime.now()
        while cursor < end and len(out) < limit:
            start = cursor.strftime('%Y-%m-%dT%H:%M')
            if cursor > now and self.slot_occupant(provider_id, start) is None:
                out.append(start)
            cursor += timedelta(minutes=settings.slot_minutes)
        return out


store = HospitalStore()
