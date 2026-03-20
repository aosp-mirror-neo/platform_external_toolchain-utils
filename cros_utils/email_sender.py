# Copyright 2019 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Utilities to send email either through SMTP or SendGMR."""


import base64
from collections.abc import Sequence
import datetime
import json
import os
from typing import Any

from cros_utils import gs


GS_PATH = "gs://crostc-chrotomation-dev-artifacts/prod_emails"


class EmailSender:
    """Utility class to send email through SMTP or SendGMR."""

    class Attachment:
        """Small class to keep track of attachment info."""

        def __init__(self, name: str, content: str) -> None:
            self.name = name
            self.content = content

    def SendGSEmail(
        self,
        subject: str,
        identifier: str,
        well_known_recipients: Sequence[str] = (),
        direct_recipients: Sequence[str] = (),
        text_body: str | None = None,
        html_body: str | None = None,
    ) -> None:
        """Enqueues an email in our gs outbox.

        These emails ultimately get sent by the machinery in
        //depot/google3/googleclient/chrome/chromeos_toolchain/mailer/mail.go.

        All emails are sent from
        `mdb.c-compiler-chrome+${identifier}@google.com`.

        Args:
            subject: email subject. Must be nonempty.
            identifier: email identifier, or the text that lands after the
                `+` in the "From" email address. Must be nonempty.
            well_known_recipients: a list of well-known recipients for the
                email. These are translated into addresses by our mailer.
                Current potential values for this are ('detective',
                'cwp-team', 'cros-team', 'mage'). Either this or
                direct_recipients must be a nonempty list.
            direct_recipients: @google.com emails to send addresses to. Either
                this or well_known_recipients must be a nonempty list.
            text_body: a 'text/plain' email body to send. Either this or
                html_body must be a nonempty string. Both may be specified
            html_body: a 'text/html' email body to send. Either this or
                text_body must be a nonempty string. Both may be specified
        """
        # `str`s act a lot like tuples/lists. Ensure that we're not accidentally
        # iterating over one of those (or anything else that's sketchy, for that
        # matter).
        if not isinstance(well_known_recipients, (tuple, list)):
            raise ValueError(
                "`well_known_recipients` is unexpectedly a %s"
                % type(well_known_recipients)
            )

        if not isinstance(direct_recipients, (tuple, list)):
            raise ValueError(
                "`direct_recipients` is unexpectedly a %s"
                % type(direct_recipients)
            )

        if not subject or not identifier:
            raise ValueError("both `subject` and `identifier` must be nonempty")

        if not (well_known_recipients or direct_recipients):
            raise ValueError(
                "either `well_known_recipients` or `direct_recipients` "
                "must be specified"
            )

        for recipient in direct_recipients:
            if not recipient.endswith("@google.com"):
                raise ValueError("All recipients must end with @google.com")

        if not (text_body or html_body):
            raise ValueError(
                "either `text_body` or `html_body` must be specified"
            )

        email_json: dict[str, Any] = {
            "email_identifier": identifier,
            "subject": subject,
        }

        if well_known_recipients:
            email_json["well_known_recipients"] = well_known_recipients

        if direct_recipients:
            email_json["direct_recipients"] = direct_recipients

        if text_body:
            email_json["body"] = text_body

        if html_body:
            email_json["html_body"] = html_body

        # The name of this has two parts:
        # - An easily sortable time, to provide uniqueness and let our emailer
        #   send things in the order they were put into the outbox.
        # - 64 bits of entropy, so two racing email sends don't clobber the same
        #   file.
        now = datetime.datetime.utcnow().isoformat("T", "seconds") + "Z"
        entropy = base64.urlsafe_b64encode(os.getrandom(8))
        entropy_str = entropy.rstrip(b"=").decode("utf-8")
        result_path = os.path.join(GS_PATH, now + "_" + entropy_str + ".json")

        # Note that gs writes are all-or-nothing and atomic; no need for
        # tempfiles.
        with gs.streaming_encoded_upload_to(result_path) as sink:
            json.dump(email_json, sink)
