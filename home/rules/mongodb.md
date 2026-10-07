---
paths:
  - "server/**/{models,schemas,repositories,db}/**/*.{ts,js}"
  - "server/**/*.{model,schema,repository}.{ts,js}"
  - "server/**/migrations/**/*.{ts,js,cjs,mjs}"
  - "server/**/migrate-mongo-config.{ts,js,cjs,mjs}"
---
# MongoDB (Mongoose or the native driver)

- Every collection has a schema: Mongoose schemas with `required`, `enum`, `min`/`max` and `maxlength`, or a `$jsonSchema` validator with the native driver. Zod at the API edge doesn't replace it.
- Query filters never take raw request values. With Mongoose set `sanitizeFilter: true` and `strictQuery: 'throw'` globally, and wrap operators you build yourself in `mongoose.trusted()`, or they turn into `$eq` and match nothing. With the driver, build filters from Zod-parsed primitives only. `express-mongo-sanitize` breaks on Express 5, where `req.query` is read-only.
- Check ids are 24-hex strings before querying (a Zod regex, or `isObjectIdOrHexString`) and answer 404, not a cast-error 500.
- Every read and write carries the requester's owner or tenant id in the same filter (`findOne({ _id, owner_id })`, `updateOne({ _id, owner_id }, …)`). Fetching by `_id` alone and checking ownership later is how BOLA bugs start.
- Repositories return plain records (`id: doc._id.toHexString()`), so `_id` and `ObjectId` never leave the data layer. Lint allows the `_id` name for this.
- Each query pattern has an index matching its filter and sort (equality, then sort, then range). Declare indexes in the schema, turn `autoIndex` off in production, and build them in a migration.
- Reads use `.lean()` and a projection of the fields the response needs. Secret fields such as password hashes are `select: false`.
- Mongoose types queries from the schema, so don't cast results with `as`. When a projection drops fields, type the query with its generic: `.lean<Pick<User, "role"> & { _id: Types.ObjectId }>()`, where `User` is `InferSchemaType<typeof userSchema>`.
- Paginate by range on an indexed, unique key (`_id`, or `created_at` plus `_id`) with a capped `limit`. `skip()` only for small, bounded admin lists.
- Writes that touch more than one document run in a transaction (`connection.transaction()` or `session.withTransaction()`) with the session passed to every operation. Transactions need a replica set, so dev and test run a single-node one.
- Counters and shared fields change with atomic operators (`$inc`, `$set`, `$push` with `$slice`), not read-modify-save.
- No unbounded arrays in a document. Anything that grows without limit (comments, events, members) gets its own collection with a reference.
- Schema and data changes ship as migrate-mongo migrations with `up` and `down`, run forward, back and forward on a scratch database before merging. Batch large updates. The test stack runs `migrate-mongo up` before the API starts, as a deploy does.
- Dates are BSON `Date` in UTC, never strings or local time. Schemas enable `timestamps`. Mongoose infers renamed timestamps only from literal options: `{ timestamps: { createdAt: "created_at", updatedAt: "updated_at" } } as const`.
- No `$where`, `$function`, `$accumulator` or `mapReduce`: no server-side JavaScript.
