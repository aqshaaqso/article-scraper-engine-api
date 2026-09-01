import threading
import time

from article_scraper_lab.rate_limiter import DomainRateLimiter


def test_same_domain_is_serialized() -> None:
    limiter = DomainRateLimiter(0.01)
    active = 0
    maximum = 0
    guard = threading.Lock()

    def work() -> None:
        nonlocal active, maximum
        with limiter.limit("example.com"):
            with guard:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.02)
            with guard:
                active -= 1

    threads = [threading.Thread(target=work) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert maximum == 1


def test_domain_specific_delay_can_follow_robots_policy() -> None:
    limiter = DomainRateLimiter(0.001)
    limiter.set_min_delay("example.com", 0.03)

    with limiter.limit("example.com"):
        pass
    started = time.monotonic()
    with limiter.limit("example.com"):
        pass

    assert time.monotonic() - started >= 0.025
