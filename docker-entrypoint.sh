#!/bin/sh
# Entry point of the application image. The image runs the server as the unprivileged user `app`, but a persistent
# disk a host mounts at /data (Render does) belongs to root and hides the directories the image made there. So the
# container starts as root, creates the data directories and gives them to `app`, then continues as `app` with
# the command. Started as `app` already (`docker run --user`), it just runs the command.
set -e
if [ "$(id -u)" = 0 ]; then
    # Only the directories themselves: everything below is created by `app`.
    mkdir -p /data/store /data/cache
    chown app:app /data /data/store /data/cache
    exec setpriv --reuid=app --regid=app --init-groups "$@"
fi
exec "$@"
