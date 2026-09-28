FROM golang:1.24.2-bookworm AS build

RUN git clone --depth 1 --branch RELEASE.2025-09-07T16-13-09Z https://github.com/minio/minio.git /src/minio \
    && test "$(git -C /src/minio rev-parse HEAD)" = "07c3a429bfed433e49018cb0f78a52145d4bedeb"
WORKDIR /src/minio
RUN flags="$(MINIO_RELEASE=RELEASE go run buildscripts/gen-ldflags.go 2025-09-07T16:13:09Z)" \
    && CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -tags kqueue -trimpath -ldflags "$flags" -o /minio .

FROM alpine:3.21.3
RUN apk add --no-cache ca-certificates
COPY --from=build /minio /usr/local/bin/minio
COPY --from=build /src/minio/LICENSE /usr/share/licenses/minio/LICENSE
COPY deploy/minio/entrypoint.sh /usr/local/bin/minio-entrypoint
RUN chmod 0755 /usr/local/bin/minio-entrypoint
EXPOSE 9000 9001
ENTRYPOINT ["/usr/local/bin/minio-entrypoint"]
