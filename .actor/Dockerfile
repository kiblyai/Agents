# Built by Apify from the repository root (dockerContextDir in actor.json). Only the lead agent's code is copied in.
FROM apify/actor-python:3.12

USER myuser

COPY --chown=myuser:myuser leadactor/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt && pip freeze

COPY --chown=myuser:myuser agentkit ./agentkit
COPY --chown=myuser:myuser leadagent ./leadagent
COPY --chown=myuser:myuser leadactor ./leadactor
RUN python -m compileall -q agentkit leadagent leadactor

CMD ["python", "-m", "leadactor", "apify"]
