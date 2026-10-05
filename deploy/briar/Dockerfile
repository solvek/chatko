# briar-headless from our fork (D29, D51): upstream plus the private-group API (design.md §7.4),
# for amd64 and arm64 (D53). The build stage runs on the build machine and makes the jars for both
# architectures from the build context `briar`, the fork's source (see deploy/docker-compose.yml);
# the runtime stage takes the jar for the target platform.
#   docker compose -f deploy/docker-compose.yml build briar
#   docker buildx build --build-context briar=../briar --platform linux/arm64 -t chatko/briar-headless deploy/briar
FROM --platform=$BUILDPLATFORM eclipse-temurin:17-jdk AS build
WORKDIR /src
# The source without Git data and build outputs, which a local checkout may have.
RUN --mount=type=bind,from=briar,target=/checkout \
    tar -C /checkout --exclude=./.git --exclude=./.gradle --exclude='./*/build' -cf - . | tar -xf -
RUN --mount=type=cache,target=/root/.gradle \
    ./gradlew --no-daemon --configure-on-demand \
        briar-headless:x86LinuxJar briar-headless:aarch64LinuxJar \
    && mkdir /out \
    && cp briar-headless/build/libs/briar-headless-linux-x86_64.jar /out/amd64.jar \
    && cp briar-headless/build/libs/briar-headless-linux-aarch64.jar /out/arm64.jar

FROM eclipse-temurin:17-jre
ARG TARGETARCH
# curl for the health check. The entrypoint starts as root only to give /data to briar, which
# matters for a bind mount that Docker created as root.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --create-home briar \
    && mkdir /data && chown briar:briar /data
COPY --from=build /out/${TARGETARCH}.jar /opt/briar-headless.jar
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
ENV BRIAR_DATA_DIR=/data BRIAR_PORT=7000
VOLUME /data
EXPOSE 7000
# The API answers 401 without a token once it is up; Tor connects later, on its own.
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s \
    CMD test "$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$BRIAR_PORT/v1/contacts")" = 401
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
