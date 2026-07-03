FROM gcc:15

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    cmake \
    ninja-build \
    pkg-config \
    libdrogon-dev \
    libjsoncpp-dev \
    libpqxx-dev \
    libpq-dev \
    postgresql-client \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . .
RUN cmake -S . -B /tmp/gatherly-build -G Ninja \
      -DGATHERLY_REQUIRE_DROGON=ON \
      -DGATHERLY_REQUIRE_POSTGRES=ON \
  && cmake --build /tmp/gatherly-build \
  && ctest --test-dir /tmp/gatherly-build --output-on-failure \
  && cp /tmp/gatherly-build/gatherly_drogon /usr/local/bin/gatherly_drogon

ENV GATHERLY_PORT=8080
ENV GATHERLY_DATABASE_URL=postgresql://gatherly:gatherly@postgres:5432/gatherly
EXPOSE 8080
CMD ["gatherly_drogon"]
