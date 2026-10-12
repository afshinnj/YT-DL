"""Chrome browser integration for YT-DL.

Three cooperating pieces, all built on top of the existing `ytdm` download
engine without modifying its behaviour:

* :mod:`protocol`      — the versioned JSON wire format spoken over Chrome
                        Native Messaging (length-prefixed UTF-8 frames).
* :mod:`download_service` — the service layer that turns protocol requests into
                        engine calls, job records and history entries.
* :mod:`native_host`   — the stdio host Chrome itself spawns; it only dispatches
                        requests and never runs a download in its own process.

:mod:`supervisor` is a detached background process that owns the download
queue. It is started on demand by the host and outlives Chrome, so closing the
browser does not abort a transfer.
"""
__version__ = "1.0.0"
