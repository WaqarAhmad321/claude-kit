# Error paths: no session (401), invalid input (400), unknown ids (404), missing role (403),
# unsupported methods (405).
# Every error is problem+json, and hostile input never gets a 5xx.

GET {{base_url}}/api/notes
HTTP 401
[Asserts]
header "Content-Type" contains "application/problem+json"
jsonpath "$.status" == 401

POST {{base_url}}/api/auth/login
{
  "email": {"$ne": null},
  "password": {"$ne": null}
}
HTTP 400
[Asserts]
header "Content-Type" contains "application/problem+json"
cookie "session" not exists

POST {{base_url}}/api/auth/signup
[Options]
variable: email=errors-{{newUuid}}@example.test
variable: password=pw-{{newUuid}}
{
  "email": "{{email}}",
  "password": "{{password}}",
  "display_name": "Journey Errors"
}
HTTP 201

POST {{base_url}}/api/auth/signup
{
  "email": "{{email}}",
  "password": "{{password}}",
  "display_name": "Same email again"
}
HTTP 409
[Asserts]
header "Content-Type" contains "application/problem+json"

POST {{base_url}}/api/auth/login
{
  "email": "{{email}}",
  "password": "wrong-{{password}}"
}
HTTP 401
[Asserts]
cookie "session" not exists

POST {{base_url}}/api/auth/login
{
  "email": "{{email}}",
  "password": "{{password}}"
}
HTTP 204

POST {{base_url}}/api/notes
{
  "title": ""
}
HTTP 400
[Asserts]
header "Content-Type" contains "application/problem+json"
jsonpath "$.status" == 400
jsonpath "$.errors[*].pointer" contains "#/title"

GET {{base_url}}/api/notes/not-an-object-id
HTTP 404
[Asserts]
header "Content-Type" contains "application/problem+json"

GET {{base_url}}/api/notes/000000000000000000000000
HTTP 404
[Asserts]
header "Content-Type" contains "application/problem+json"

GET {{base_url}}/api/admin/users
HTTP 403
[Asserts]
header "Content-Type" contains "application/problem+json"

PUT {{base_url}}/api/notes/000000000000000000000000
{
  "title": "Replaced"
}
HTTP 405
[Asserts]
header "Allow" exists
header "Content-Type" contains "application/problem+json"

POST {{base_url}}/api/notes
Content-Type: application/json
```
{"title": "unterminated
```
HTTP *
[Asserts]
status >= 400
status < 500

GET {{base_url}}/api/notes?limit[$gt]=0&cursor=%00
HTTP *
[Asserts]
status < 500

GET {{base_url}}/api/notes?limit=100000
HTTP *
[Asserts]
status < 500
