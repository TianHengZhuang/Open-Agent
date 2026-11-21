"""Retry/backoff behaviour for worker jobs."""

from worker.queue import BackoffStrategy, Job, RetryPolicy


class TestRetryPolicy:
    def test_fixed_backoff(self):
        policy = RetryPolicy(
            max_attempts=3,
            base_delay_seconds=1.0,
            strategy=BackoffStrategy.FIXED,
        )
        job = Job(payload={}, retry_policy=policy)
        job.attempts = 1
        assert job.calculate_next_retry_delay() == 1.0
        job.attempts = 2
        assert job.calculate_next_retry_delay() == 1.0
        job.attempts = 3
        assert job.calculate_next_retry_delay() == 1.0

    def test_linear_backoff(self):
        policy = RetryPolicy(
            max_attempts=3,
            base_delay_seconds=2.0,
            strategy=BackoffStrategy.LINEAR,
        )
        job = Job(payload={}, retry_policy=policy)
        job.attempts = 1
        assert job.calculate_next_retry_delay() == 2.0
        job.attempts = 2
        assert job.calculate_next_retry_delay() == 4.0

    def test_exponential_backoff(self):
        policy = RetryPolicy(
            max_attempts=3,
            base_delay_seconds=1.0,
            strategy=BackoffStrategy.EXPONENTIAL,
        )
        job = Job(payload={}, retry_policy=policy)
        job.attempts = 1
        assert job.calculate_next_retry_delay() == 1.0
        job.attempts = 2
        assert job.calculate_next_retry_delay() == 2.0
        job.attempts = 3
        assert job.calculate_next_retry_delay() == 4.0

    def test_exponential_jitter(self):
        policy = RetryPolicy(
            max_attempts=3,
            base_delay_seconds=1.0,
            strategy=BackoffStrategy.EXPONENTIAL_JITTER,
        )
        job = Job(payload={}, retry_policy=policy)
        job.attempts = 1
        assert 0.5 <= job.calculate_next_retry_delay() <= 1.5
        job.attempts = 2
        assert 1.0 <= job.calculate_next_retry_delay() <= 3.0
