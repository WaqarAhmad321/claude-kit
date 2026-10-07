# Error paths: no token (401), invalid input (400), another user's note (404), a missing role (403),
# an unsupported method (405) and a duplicate (409). Every error is problem+json, and none is a 5xx.

GET {{base_url}}/api/v1/notes/
HTTP 401
[Asserts]
header "Content-Type" contains "application/problem+json"
jsonpath "$.status" == 401

POST {{base_url}}/api/v1/auth/signup/
[Options]
variable: email=errors-{{newUuid}}@example.test
variable: password=pw-{{newUuid}}
{
  "email": "{{email}}",
  "password": "{{password}}",
  "display_name": "Journey Errors"
}
HTTP 201

POST {{base_url}}/api/v1/auth/signup/
{
  "email": "{{email}}",
  "password": "{{password}}",
  "display_name": "Same email again"
}
HTTP 409
[Asserts]
header "Content-Type" contains "application/problem+json"

POST {{base_url}}/api/v1/auth/token/
{
  "email": "{{email}}",
  "password": "wrong-{{password}}"
}
HTTP 401

POST {{base_url}}/api/v1/auth/token/
{
  "email": "{{email}}",
  "password": "{{password}}"
}
HTTP 200
[Captures]
access: jsonpath "$.access"

POST {{base_url}}/api/v1/notes/
Authorization: Bearer {{access}}
{
  "title": ""
}
HTTP 400
[Asserts]
header "Content-Type" contains "application/problem+json"
jsonpath "$.errors[*].attr" contains "title"

PUT {{base_url}}/api/v1/notes/
Authorization: Bearer {{access}}
{}
HTTP 405

GET {{base_url}}/api/v1/admin/users/
Authorization: Bearer {{access}}
HTTP 403
[Asserts]
header "Content-Type" contains "application/problem+json"

# Another user's note answers 404, so its existence doesn't leak.
POST {{base_url}}/api/v1/auth/signup/
[Options]
variable: other_email=other-{{newUuid}}@example.test
variable: other_password=pw-{{newUuid}}
{
  "email": "{{other_email}}",
  "password": "{{other_password}}",
  "display_name": "Other User"
}
HTTP 201

POST {{base_url}}/api/v1/auth/token/
{
  "email": "{{other_email}}",
  "password": "{{other_password}}"
}
HTTP 200
[Captures]
other_access: jsonpath "$.access"

POST {{base_url}}/api/v1/notes/
Authorization: Bearer {{other_access}}
{
  "title": "Not yours"
}
HTTP 201
[Captures]
other_note_url: header "Location"

GET {{base_url}}{{other_note_url}}
Authorization: Bearer {{access}}
HTTP 404

PATCH {{base_url}}{{other_note_url}}
Authorization: Bearer {{access}}
Content-Type: application/merge-patch+json
{
  "title": "Taken over"
}
HTTP 404
