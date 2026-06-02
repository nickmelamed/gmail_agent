FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY web/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Shared modules from project root
COPY rank_reply.py core.py profile_schema.py tools.py profile.txt ./

# Web app
COPY web/ .

CMD ["gunicorn", "-b", ":8080", "app:app"]