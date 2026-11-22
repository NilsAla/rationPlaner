ARG BUILD_FROM
FROM ${BUILD_FROM}

RUN apk add --no-cache sqlite

WORKDIR /opt/ration
COPY pyproject.toml ./
COPY app ./app
RUN pip install --no-cache-dir .

COPY rootfs /
COPY run.sh /run.sh
RUN chmod a+x /run.sh /usr/local/bin/ration-backup

CMD ["/run.sh"]
