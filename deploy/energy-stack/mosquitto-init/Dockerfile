FROM eclipse-mosquitto:2

# eclipse-mosquitto:2 already includes mosquitto_passwd. The container runs
# once per `compose up`, generates a fresh password file from env vars, and
# exits. Idempotent.

WORKDIR /init
COPY init-passwords.sh /init/init-passwords.sh

# Run as root so we can chown the output to the mosquitto runtime user
# (1883:1883 in eclipse-mosquitto:2). The broker container runs unprivileged.
USER root

ENTRYPOINT ["/bin/sh", "/init/init-passwords.sh"]
