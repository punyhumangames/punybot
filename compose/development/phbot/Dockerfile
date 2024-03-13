ARG PYTHON_VERSION=3.11.6-slim-bookworm
# define an alias for the specfic python version used in this file.
FROM python:${PYTHON_VERSION} as python

FROM python as python-poetry-stage
ARG BUILD_ENVIRONMENT=development
RUN apt-get update && apt-get install --no-install-recommends -y curl
RUN curl -sSL install.python-poetry.org | POETRY_HOME=/opt/poetry python -
ENV PATH /opt/poetry/bin:$PATH
COPY poetry.lock poetry.lock
COPY pyproject.toml pyproject.toml
RUN poetry export --without-hashes > /tmp/requirements.txt

# Python build stage
FROM python as python-build-stage
ARG BUILD_ENVIRONMENT=development
# Install apt packages
RUN apt-get update && apt-get install --no-install-recommends -y \
  # dependencies for building Python packages
  build-essential \
  # psycopg2 dependencies
  libpq-dev \
  # git
  git
 \
    # Requirements are installed here to ensure they will be cached.
COPY --from=python-poetry-stage /tmp/requirements.txt .

# Create Python Dependency and Sub-Dependency Wheels.
RUN pip wheel --wheel-dir /usr/src/app/wheels  \
  -r requirements.txt

# Python 'run' stage
FROM python as python-run-stage
ARG BUILD_ENVIRONMENT=development
ARG APP_HOME=/app
ENV PYTHONUNBUFFERED 1
ENV PYTHONDONTWRITEBYTECODE 1
ENV BUILD_ENV ${BUILD_ENVIRONMENT}
ENV DISCORD_TOKEN ${DISCORD_TOKEN}
ENV TWITTER_BEARER_TOKEN ${TWITTER_BEARER_TOKEN}
ENV STEAM_API_KEY ${STEAM_API_KEY}
WORKDIR ${APP_HOME}
RUN addgroup --system phbot \
    && adduser --system --ingroup phbot phbot
# Install required system dependencies
RUN apt-get update && apt-get install --no-install-recommends -y \
  # cleaning up unused files
  && apt-get purge -y --auto-remove -o APT::AutoRemove::RecommendsImportant=false \
  && rm -rf /var/lib/apt/lists/*
# All absolute dir copies ignore workdir instruction. All relative dir copies are wrt to the workdir instruction
# copy python dependency wheels from python-build-stage
COPY --from=python-poetry-stage /tmp/requirements.txt .
COPY --from=python-build-stage /usr/src/app/wheels  /wheels/
# use wheels to install python dependencies
RUN pip install --no-cache-dir --no-index --find-links=/wheels/ /wheels/* \
  && rm -rf /wheels/
COPY --chown=phbot:phbot ./compose/development/phbot/entrypoint /entrypoint
RUN sed -i 's/\r$//g' /entrypoint
RUN chmod +x /entrypoint
COPY --chown=phbot:phbot ./compose/development/phbot/start /start
RUN sed -i 's/\r$//g' /start
RUN chmod +x /start
# Ensure application permissions are given to the user.
RUN chown phbot:phbot ${APP_HOME}
# create data directory and set permissions
# make phbot owner of the WORKDIR directory as well.
RUN chown phbot:phbot ${APP_HOME}
USER phbot
ENTRYPOINT ["/entrypoint"]
