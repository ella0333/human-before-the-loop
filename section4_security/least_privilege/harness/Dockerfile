FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates git openssh-server \
    && curl -fsSL https://deb.nodesource.com/setup_24.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*


WORKDIR /app
COPY requirements-container.txt /app/requirements-container.txt
COPY constrained /app/constrained

RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch \
    && pip install --no-cache-dir -r requirements-container.txt \
    && pip install --no-cache-dir -e ./constrained

ARG OPENCLAW_VERSION=2026.7.1-2
RUN npm install -g openclaw@${OPENCLAW_VERSION}

COPY openclaw/plugins/mock-discord /app/openclaw/plugins/mock-discord
COPY openclaw/plugins/mock-activities /app/openclaw/plugins/mock-activities
RUN mkdir -p /app/openclaw/packages \
    && cd /app/openclaw/plugins/mock-discord && npm install --include=dev && npm run build && npm pack --pack-destination /app/openclaw/packages \
    && cd /app/openclaw/plugins/mock-activities && npm install --include=dev && npm run build && npm pack --pack-destination /app/openclaw/packages \
    && openclaw plugins install npm-pack:/app/openclaw/packages/injection-eval-mock-discord-1.0.0.tgz --force \
    && openclaw plugins install npm-pack:/app/openclaw/packages/injection-eval-mock-activities-1.0.0.tgz --force \
    && openclaw plugins inspect mock-discord --runtime --json \
    && openclaw plugins inspect mock-activities --runtime --json

RUN python -c "from pytector import PromptInjectionDetector; PromptInjectionDetector(model_name_or_url='deberta')"

COPY . /app

RUN sed -i 's/#\?PermitRootLogin.*/PermitRootLogin prohibit-password/' /etc/ssh/sshd_config \
    && sed -i 's/#\?PubkeyAuthentication.*/PubkeyAuthentication yes/' /etc/ssh/sshd_config
COPY start.sh /start.sh
RUN chmod +x /start.sh

CMD ["/start.sh"]
