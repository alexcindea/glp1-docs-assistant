# Postgres 18 with pgvector (from the base image) and pg_textsearch (BM25 ranking).
#
# pg_textsearch is installed from its prebuilt release package and checked against
# the sha256 published on the GitHub release. When bumping the version, update the
# two checksums below with it.
ARG PG_TEXTSEARCH_VERSION=1.5.1

FROM alpine:3.22 AS fetch
ARG PG_TEXTSEARCH_VERSION
RUN set -eu; \
    case "$(apk --print-arch)" in \
      x86_64)  arch=amd64; sha=6adc4c390c61109af337c5d5c15239623d6fc8b89e972625de04f52a5771cebf ;; \
      aarch64) arch=arm64; sha=54843473cbad144812be0437bc708233087c7823018700ffb99a2fb92d1466bb ;; \
      *) echo "unsupported architecture: $(apk --print-arch)" >&2; exit 1 ;; \
    esac; \
    wget -q -O /tmp/pg_textsearch.zip \
      "https://github.com/timescale/pg_textsearch/releases/download/v${PG_TEXTSEARCH_VERSION}/pg-textsearch-v${PG_TEXTSEARCH_VERSION}-pg18-${arch}.zip"; \
    echo "${sha}  /tmp/pg_textsearch.zip" | sha256sum -c -; \
    mkdir /out; \
    unzip -q /tmp/pg_textsearch.zip -d /out

FROM pgvector/pgvector:0.8.7-pg18-trixie
COPY --from=fetch /out/*.deb /tmp/
RUN DEBIAN_FRONTEND=noninteractive dpkg -i /tmp/*.deb && rm /tmp/*.deb

# pg_textsearch must be preloaded, or CREATE EXTENSION fails. Setting it in the
# default config means every new database gets it, even if the start command is
# overridden. The grep makes the build fail if the sed stops matching.
RUN sed -i "s/^#shared_preload_libraries = ''/shared_preload_libraries = 'pg_textsearch'/" \
      /usr/share/postgresql/postgresql.conf.sample \
 && grep -q "^shared_preload_libraries = 'pg_textsearch'" /usr/share/postgresql/postgresql.conf.sample
