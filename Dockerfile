FROM langgenius/dify-api:1.7.2

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PATH=/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin \
    VIRTUAL_ENV=

WORKDIR /app

ENTRYPOINT []

COPY requirements.txt /app/requirements.txt

RUN /usr/local/bin/pip install --upgrade pip \
    && /usr/local/bin/pip install -r /app/requirements.txt

COPY . /app

EXPOSE 8000

CMD ["/usr/local/bin/python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
