"""Tool schemas offered to the model, and the executors that run them.

Two things to notice:

  * Every executor here assumes the call has ALREADY been approved. There is no
    validation in these functions -- that is the whole point. All policy lives
    in rules_hospital/, so you can read the guardrails in one place instead of
    hunting through handler code.

  * The read-only tools (list_providers, find_open_slots) exist to give the
    model a way to be correct. Without them an 8B model invents provider names
    and appointment times, every call gets denied, and you learn nothing about
    the guardrails except that they fire.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Dict, List

from .hospital import store

# Tools that change hospital state. These are the ones that must clear the
# Osprey gate before execution.
WRITE_TOOLS = {
    'book_appointment',
    'reschedule_appointment',
    'cancel_appointment',
    'onboard_patient',
    'update_patient_record',
}

# Tools that touch a patient record at all (read or write). Gated too, because
# reading someone else's appointments is a PHI leak.
GATED_TOOLS = WRITE_TOOLS | {'lookup_appointments'}


TOOL_SCHEMAS: List[Dict[str, Any]] = [
    {
        'type': 'function',
        'function': {
            'name': 'list_providers',
            'description': (
                'List the clinicians at this practice with their department and daily '
                'appointment cap. Call this before booking so you use a real provider id.'
            ),
            'parameters': {'type': 'object', 'properties': {}, 'required': []},
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'find_open_slots',
            'description': (
                'Find genuinely free appointment slots for a provider on a given date. '
                'Always call this before book_appointment -- never guess a time.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'provider_id': {'type': 'string', 'description': 'e.g. dr-okafor'},
                    'date': {'type': 'string', 'description': 'YYYY-MM-DD'},
                },
                'required': ['provider_id', 'date'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'lookup_appointments',
            'description': "Look up the signed-in patient's upcoming appointments.",
            'parameters': {
                'type': 'object',
                'properties': {
                    'patient_id': {'type': 'string', 'description': 'Patient id to look up'},
                },
                'required': ['patient_id'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'book_appointment',
            'description': 'Book an appointment in a slot confirmed free by find_open_slots.',
            'parameters': {
                'type': 'object',
                'properties': {
                    'patient_id': {'type': 'string'},
                    'provider_id': {'type': 'string'},
                    'start': {
                        'type': 'string',
                        'description': 'Slot start, exactly as returned by find_open_slots (YYYY-MM-DDTHH:MM)',
                    },
                    'department': {'type': 'string'},
                },
                'required': ['patient_id', 'provider_id', 'start'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'reschedule_appointment',
            'description': 'Move an existing appointment to a different free slot.',
            'parameters': {
                'type': 'object',
                'properties': {
                    'appointment_id': {'type': 'string'},
                    'provider_id': {'type': 'string'},
                    'start': {'type': 'string', 'description': 'New start, YYYY-MM-DDTHH:MM'},
                },
                'required': ['appointment_id', 'start'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'cancel_appointment',
            'description': 'Cancel an existing appointment.',
            'parameters': {
                'type': 'object',
                'properties': {'appointment_id': {'type': 'string'}},
                'required': ['appointment_id'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'onboard_patient',
            'description': (
                'Register a new patient. Requires full name, date of birth, phone, and '
                'explicit consent. Do not put medical information in notes.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'full_name': {'type': 'string'},
                    'date_of_birth': {'type': 'string', 'description': 'YYYY-MM-DD'},
                    'phone': {'type': 'string'},
                    'email': {'type': 'string'},
                    'insurance_id': {'type': 'string'},
                    'consent_given': {
                        'type': 'boolean',
                        'description': 'True only if the patient explicitly agreed',
                    },
                    'notes': {
                        'type': 'string',
                        'description': 'Administrative notes only, e.g. scheduling preferences',
                    },
                },
                'required': ['full_name', 'date_of_birth', 'phone', 'consent_given'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'update_patient_record',
            'description': (
                'Update one contact or administrative field on a patient record. '
                'Permitted fields: phone, email, address, insurance_id, '
                'preferred_language, emergency_contact.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'patient_id': {'type': 'string'},
                    'field': {'type': 'string'},
                    'value': {'type': 'string'},
                },
                'required': ['patient_id', 'field', 'value'],
            },
        },
    },
]


# ---------------------------------------------------------------------------
# Executors -- reached only after the gate allows the call
# ---------------------------------------------------------------------------


def execute(tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return _EXECUTORS[tool_name](args)
    except KeyError:
        return {'ok': False, 'error': f'unknown tool: {tool_name}'}
    except Exception as e:
        # Surfaced back to the model so it can tell the patient something true.
        return {'ok': False, 'error': str(e)}


def _list_providers(_: Dict[str, Any]) -> Dict[str, Any]:
    return {'ok': True, 'providers': store.provider_summary()}


def _find_open_slots(args: Dict[str, Any]) -> Dict[str, Any]:
    provider_id = args.get('provider_id', '')

    # An unknown provider must not look like a fully-booked one. Returning an
    # empty list for a hallucinated id led the agent to tell a patient "the
    # nurse is fully booked today" about someone who does not exist -- a false
    # statement that no guardrail would catch, because the reply is not
    # clinical and no write was attempted. Name the real providers so the model
    # can correct itself.
    if provider_id not in store.providers:
        return {
            'ok': False,
            'error': f'No provider with id {provider_id!r}. Use one of the ids below.',
            'valid_providers': [
                {'id': p.id, 'name': p.name, 'department': p.department}
                for p in store.providers.values()
            ],
        }

    slots = store.open_slots(provider_id, args.get('date', ''))
    return {
        'ok': True,
        'provider_id': provider_id,
        'date': args.get('date'),
        'open_slots': slots,
        'note': 'No free slots on this date.' if not slots else None,
    }


def _lookup_appointments(args: Dict[str, Any]) -> Dict[str, Any]:
    apts = store.appointments_for(args.get('patient_id', ''))
    return {'ok': True, 'appointments': [asdict(a) for a in apts]}


def _book(args: Dict[str, Any]) -> Dict[str, Any]:
    apt = store.book(
        patient_id=args['patient_id'],
        provider_id=args['provider_id'],
        start=args['start'],
        department=args.get('department') or store.providers[args['provider_id']].department,
    )
    return {'ok': True, 'appointment': asdict(apt)}


def _reschedule(args: Dict[str, Any]) -> Dict[str, Any]:
    return {'ok': True, 'appointment': asdict(store.reschedule(args['appointment_id'], args['start']))}


def _cancel(args: Dict[str, Any]) -> Dict[str, Any]:
    return {'ok': True, 'appointment': asdict(store.cancel(args['appointment_id']))}


def _onboard(args: Dict[str, Any]) -> Dict[str, Any]:
    patient = store.create_patient(
        full_name=args['full_name'],
        date_of_birth=args['date_of_birth'],
        phone=args['phone'],
        email=args.get('email', ''),
        insurance_id=args.get('insurance_id', ''),
        consent_given=bool(args.get('consent_given')),
        notes=args.get('notes', ''),
    )
    return {'ok': True, 'patient': asdict(patient)}


def _update_record(args: Dict[str, Any]) -> Dict[str, Any]:
    patient = store.update_patient(args['patient_id'], args['field'], args['value'])
    return {'ok': True, 'patient': asdict(patient)}


_EXECUTORS = {
    'list_providers': _list_providers,
    'find_open_slots': _find_open_slots,
    'lookup_appointments': _lookup_appointments,
    'book_appointment': _book,
    'reschedule_appointment': _reschedule,
    'cancel_appointment': _cancel,
    'onboard_patient': _onboard,
    'update_patient_record': _update_record,
}
