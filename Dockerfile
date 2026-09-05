FROM gcc:15 AS build
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates cmake ninja-build pkg-config libssl-dev libdrogon-dev \
    libjsoncpp-dev libpqxx-dev libpq-dev libgtest-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /source
COPY CMakeLists.txt ./
COPY src/ src/
COPY tests/ tests/
RUN cmake -S . -B /build -G Ninja -DCMAKE_BUILD_TYPE=Release \
      -DGATHERLY_REQUIRE_DROGON=ON -DGATHERLY_REQUIRE_POSTGRES=ON \
    && cmake --build /build --parallel 2 \
    && ctest --test-dir /build --output-on-failure

FROM debian:trixie-slim AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl libdrogon1t64 libpqxx-7.10 libssl3t64 libstdc++6 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 10001 --create-home gatherly
WORKDIR /app
COPY --from=build /build/gatherly_drogon /usr/local/bin/gatherly
COPY static/ static/
USER gatherly
ENV GATHERLY_PORT=8080
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=7s --start-period=20s --retries=3 \
    CMD curl --fail --silent --max-time 6 http://127.0.0.1:8080/ready || exit 1
CMD ["gatherly"]
