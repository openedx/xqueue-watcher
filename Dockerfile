FROM ubuntu:xenial as openedx

RUN apt update && \
  apt install -y git-core language-pack-en apparmor apparmor-utils python python-pip python-dev && \
  pip install --upgrade pip setuptools && \
  rm -rf /var/lib/apt/lists/*

RUN locale-gen en_US.UTF-8
ENV LANG en_US.UTF-8
ENV LANGUAGE en_US:en
ENV LC_ALL en_US.UTF-8

WORKDIR /edx/app/xqueue_watcher
COPY requirements /edx/app/xqueue_watcher/requirements
RUN pip install -r requirements/production.txt

CMD python -m xqueue_watcher -d /edx/etc/xqueue_watcher

RUN useradd -m --shell /bin/false app
USER app

COPY . /edx/app/xqueue_watcher

FROM openedx as edx.org
RUN pip install newrelic
CMD newrelic-admin run-program python -m xqueue_watcher -d /edx/etc/xqueue_watcher
