FROM python:3.11-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates openssh-server libgl1 libglib2.0-0 libmgba0.10 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements-container.txt /app/requirements-container.txt
RUN pip install --no-cache-dir -r requirements-container.txt

COPY . /app
RUN sed -i 's/#\?PermitRootLogin.*/PermitRootLogin prohibit-password/' /etc/ssh/sshd_config \
    && sed -i 's/#\?PubkeyAuthentication.*/PubkeyAuthentication yes/' /etc/ssh/sshd_config
COPY start.sh /start.sh
RUN chmod +x /start.sh

CMD ["/start.sh"]
