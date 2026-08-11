# 3.11, not 3.12+: kafka-python 2.0.2 imports `kafka.vendor.six.moves`, which
# breaks on 3.12 ("No module named 'kafka.vendor.six.moves'"). The Osprey worker
# is on 3.11 for the same library, so this keeps both sides matched.
FROM python:3.11-slim

WORKDIR /srv

COPY osprey-hospital/gateway/requirements.txt /srv/requirements.txt
RUN pip install --no-cache-dir -r /srv/requirements.txt

COPY osprey-hospital/gateway /srv/gateway

ENV PYTHONUNBUFFERED=1
EXPOSE 8080

CMD ["uvicorn", "gateway.app.main:app", "--host", "0.0.0.0", "--port", "8080"]
