FROM python:3.14-slim@sha256:d3400aa122fa42cf0af0dbe8ec3091b047eac5c8f7e3539f7135e86d855dc015

# Passed at build time (e.g. --build-arg GIT_COMMIT=$(git rev-parse --short HEAD))
# so the running dashboard can show which commit is actually deployed - .git
# is excluded from the build context (.dockerignore), so this can't be
# recovered at runtime any other way.
ARG GIT_COMMIT=unknown

ENV DEBIAN_FRONTEND=noninteractive \
    GIT_COMMIT=${GIT_COMMIT}
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    r-base \
    r-base-dev \
    build-essential \
    gcc \
    g++ \
    make \
    curl \
    ca-certificates \
    git \
    libcurl4-openssl-dev \
    libssl-dev \
    libxml2-dev \
    libfontconfig1-dev \
    libharfbuzz-dev \
    libfribidi-dev \
    libfreetype6-dev \
    libpng-dev \
    libtiff5-dev \
    libjpeg62-turbo-dev \
    libsqlite3-dev \
    && rm -rf /var/lib/apt/lists/*

COPY python_app/requirements.txt /app/python_app/requirements.txt
RUN pip install --no-cache-dir -r /app/python_app/requirements.txt

RUN Rscript -e 'install.packages(c("baseballr","DBI","RSQLite","jsonlite"), repos="https://cloud.r-project.org")'

COPY . /app
RUN mkdir -p /app/data

WORKDIR /app/python_app
CMD ["python3", "dashboard.py", "--host", "0.0.0.0", "--port", "8000"]
