# briar-headless from our fork (D29): upstream plus the private-group API (design.md §7.4).
# The build stage runs on the build machine and makes the jars for both architectures from the
# build context `briar`, the fork's source (see docker-compose.yml); the runtime stage takes the
# jar for the target.
#   docker compose -f lab/briar/docker-compose.yml build
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
RUN useradd --system --create-home briar \
    && mkdir /data && chown briar:briar /data
COPY --from=build /out/${TARGETARCH}.jar /opt/briar-headless.jar
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
USER briar
ENV BRIAR_DATA_DIR=/data BRIAR_PORT=7000
VOLUME /data
EXPOSE 7000
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
