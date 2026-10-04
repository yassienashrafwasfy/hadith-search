# Verified against: backend/limiter.py, tests/test_limiter.py, nginx/default.conf,
# tools/test-nginx.sh, frontend/src/api/errors.ts
# The nginx scenarios are checked by tools/test-nginx.sh against a stub app, not the real image.
Feature: Search spikes are queued, then refused with 503
  Only GET /api/v1/searches is queued. nginx smooths each client address; the app limits
  all clients of one process together. Sizes come from SEARCH_MAX_CONCURRENT (8),
  SEARCH_QUEUE_SIZE (64) and SEARCH_QUEUE_TIMEOUT_SECONDS (5).

  Scenario: Searches up to the limit run at once, the rest wait in order
    Given the app allows N searches at once and a queue of M
    When more than N searches arrive
    Then N run and the others wait first come, first served

  Scenario: A full queue is refused at once
    Given every slot is busy and the queue is full
    When another search arrives
    Then the answer is 503 with a Retry-After header
    And the content type is application/problem+json
    And the body status is 503
    And the Retry-After value is the queue timeout rounded up (a 2.2 s timeout gives "3")

  Scenario: A waiter gives up after the timeout
    Given a search is waiting for a slot
    When it waits longer than the timeout
    Then it gets the same 503 and its place in the queue is freed

  Scenario: A slot is freed even when the work fails
    Given a search holds a slot
    When its handler raises
    Then the slot is released for the next request

  Scenario: The slot is free again once the work ends
    Given the queue was full and the running search finishes
    When a new search arrives
    Then it is answered with 200

  Scenario: An app without a limiter does not limit
    Given the app has no search_limiter in its state
    Then searches are never queued or refused

  Scenario: The limiter is built from settings
    Given SEARCH_MAX_CONCURRENT, SEARCH_QUEUE_SIZE and SEARCH_QUEUE_TIMEOUT_SECONDS are set
    When the app is created
    Then app.state.search_limiter uses those values

  @nginx
  Scenario: nginx smooths a spike from one client address
    Given the app is behind nginx
    When one address sends 60 searches in a burst
    Then some answer 200 and the excess answers 503
    And each nginx 503 is application/problem+json with "Retry-After: 5" and a body with status 503

  @manual
  Scenario: The web page shows an Arabic busy message
    When the search call returns 503
    Then the page shows "الخادم مشغول الآن بسبب كثرة الطلبات. انتظر لحظات ثم حاول مرة أخرى."
    And the number 503 is not shown
