# Verified against: backend/routers/search.py, backend/services/retrieval.py,
# tests/test_search_api.py, tests/test_app_factory.py
Feature: Search the hadith corpus
  Anyone can search with one of the retrieval methods the server has switched on.
  Search needs no token.

  Background:
    Given the app runs with the search feature on
    And the corpus, the BM25 index and the embeddings are loaded

  Scenario Outline: Each method answers a query
    When a client sends GET /api/v1/searches with q "<query>", method "<method>" and lang "<lang>"
    Then the status is 200
    And number_of_results equals the length of results
    And the Server-Timing header starts with "search;dur="

    Examples: lexical methods (English or Arabic)
      | method          | query  | lang |
      | term-overlap    | prayer | en   |
      | tfidf           | prayer | en   |
      | bm25            | prayer | en   |
      | bm25-tf-idf     | prayer | en   |
      | bm25-prf        | prayer | en   |

    Examples: dense methods (Arabic only)
      | method            | query | lang |
      | semantic-rerank   | صلاه  | ar   |
      | cosine-similarity | صلاه  | ar   |
      | semantic-rrf      | صلاه  | ar   |

  Scenario: Results come back ranked
    When a client searches "prayer" with method "bm25"
    Then each result has a hadith and a score
    And the scores are in descending order
    And the body has no response_time_ms field, because timing is in the Server-Timing header

  Scenario: Results can be filtered by book and grade
    When a client searches "fasting" with method "bm25" and book_filter "Muslim"
    Then only hadiths from the Muslim book are returned
    When a client searches "fasting" with method "bm25" and grade_filter "Da'if (Weak)"
    Then number_of_results is 0

  Scenario: Dense methods refuse English queries
    When a client searches "prayer" with method "semantic-rrf" and lang "en"
    Then the status is 422

  Scenario Outline: Bad requests are refused with a problem+json 422
    When a client sends GET /api/v1/searches with <params>
    Then the status is 422
    And the content type is application/problem+json

    Examples:
      | params                       |
      | q=x, method=bm25, lang=fr    |
      | q empty, method=bm25         |
      | method=bm25 and no q         |
      | q=x and no method            |
      | q=x, method=nope             |

  Scenario: Search results are cacheable
    Given a client has fetched a search once
    Then the response has "Cache-Control: public, max-age=300" and an ETag
    When the client repeats it with If-None-Match set to that ETag
    Then the status is 304 and the body is empty

  Scenario: Results carry links
    When a client searches "fasting" with method "bm25" and book_filter "Muslim"
    Then _links.self.href is "/api/v1/searches?q=fasting&method=bm25&lang=en&book_filter=Muslim"
    And _links.methods.href is "/api/v1/search-methods"

  Scenario: POST is refused
    When a client sends POST /api/v1/searches
    Then the status is 405
    And the content type is application/problem+json

  Scenario: The server lists the methods it offers
    When a client sends GET /api/v1/search-methods
    Then each method has a slug, its languages and a templated search link
    And "semantic-rrf" lists languages ["ar"]
    And "bm25" lists languages ["en", "ar"]

  Scenario: Feature flags decide which methods exist
    Given dense_retrieval is off
    Then the methods offered are term-overlap, tfidf, bm25, bm25-tf-idf and bm25-prf
    Given search is off
    Then no method is offered and no /api/v1/search route is mounted
