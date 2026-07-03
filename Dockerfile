FROM python:3.12-slim

ENV DEBIAN_FRONTEND=noninteractive
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
