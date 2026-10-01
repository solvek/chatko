# briar-headless built from upstream (spike S3). The build stage runs on the build machine and makes
# the jars for both architectures; the runtime stage takes the one for the target.
#   docker compose -f lab/briar/docker-compose.yml build
ARG BRIAR_REF=release-1.5.21

FROM --platform=$BUILDPLATFORM eclipse-temurin:17-jdk AS build
ARG BRIAR_REF
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
WORKDIR /src
RUN git clone --depth 1 --branch "$BRIAR_REF" https://code.briarproject.org/briar/briar.git .
RUN --mount=type=cache,target=/root/.gradle \
    ./gradlew --no-daemon --configure-on-demand \
        briar-headless:x86LinuxJar briar-headless:aarch64LinuxJar \
    && mkdir /out \
    && cp briar-headless/build/libs/briar-headless-linux-x86_64.jar /out/amd64.jar \
    && cp briar-headless/build/libs/briar-headless-linux-aarch64.jar /out/arm64.jar

FROM eclipse-temurin:17-jre
ARG TARGETARCH
RUN useradd --system --create-home briar \
    && mkdir /data && chown briar:briar /data
COPY --from=build /out/${TARGETARCH}.jar /opt/briar-headless.jar
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
USER briar
ENV BRIAR_DATA_DIR=/data BRIAR_PORT=7000
VOLUME /data
EXPOSE 7000
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
