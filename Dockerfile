FROM python:3.7-alpine

RUN apk add git --no-cache && pip install virtualenv

WORKDIR /app

COPY requirements requirements

RUN pip install -r requirements/production.txt

COPY . .

RUN cp xqueue_watcher/settings.py settings.py

CMD python -m xqueue_watcher -d .
