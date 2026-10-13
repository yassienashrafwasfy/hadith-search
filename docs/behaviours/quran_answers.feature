# Verified against: backend/routers/quran.py, nginx/default.conf, tests/quran/test_api.py
Feature: Quran questions get a grounded answer or the fixed fallback sentence
  POST /api/v1/answers is served only when the quran_rag feature is on. Citation text comes
  from the stores, never from the model. Generation has its own small queue (QURAN_MAX_CONCURRENT,
  QURAN_QUEUE_SIZE, QURAN_QUEUE_TIMEOUT_SECONDS).

  Scenario: A question gets an answer with citations and a trace
    Given the Quran agent answers with one citation
    When a question is posted
    Then the status is 200
    And the answer carries the citation text and a trace

  Scenario: An empty question is refused
    Given the Quran agent answers with one citation
    When an empty question is posted
    Then the status is 422
    And the content type is application/problem+json

  Scenario: A failing agent gives the fallback sentence
    Given the Quran agent crashes
    When a question is posted
    Then the status is 200
    And the answer is the fallback sentence

  Scenario: A full answer queue is refused with 503
    Given the Quran answer queue is full
    When a question is posted
    Then the status is 503
    And the content type is application/problem+json

  @nginx
  Scenario: nginx limits answers per client address
    When one address posts many questions in a minute
    Then the excess is answered 503 as application/problem+json
