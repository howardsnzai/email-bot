from jason import poller


class FakeMailer:
    def __init__(self):
        self.processed = []

    def fetch_message(self, message_id):
        return {
            "id": message_id,
            "thread_id": "thread-1",
            "from": "Google <no-reply@accounts.google.com>",
            "from_email": "no-reply@accounts.google.com",
            "subject": "Security alert",
            "body": "New sign-in",
            "sent_by_bot": False,
        }

    def mark_processed(self, message_id):
        self.processed.append(message_id)


def test_process_message_ignores_automated_senders(store):
    mailer = FakeMailer()

    poller.process_message(store, mailer, "msg-1")

    assert mailer.processed == ["msg-1"]
    assert store.get_job("thread-1") is None
