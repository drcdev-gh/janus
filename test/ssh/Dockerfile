FROM ubuntu:22.04

RUN apt-get update && \
    apt-get install -y openssh-server curl && \
    mkdir /var/run/sshd

# Copy ssh config + script
COPY sshd_config /etc/ssh/sshd_config
COPY verify_key.sh /usr/local/bin/verify_key.sh

RUN chmod 755 /usr/local/bin/verify_key.sh && \
    chown root:root /usr/local/bin/verify_key.sh

RUN useradd -m -s /bin/sh oidc

EXPOSE 22

CMD ["/usr/sbin/sshd", "-D", "-e"]
