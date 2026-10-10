# Vendored engine

This directory contains the multi-agent research engine that backs the
pipeline: the shared research core (`gptr/`) and the LangGraph agent team
(`multi_agents/`).

## License

The code in this directory is derived from an Apache License 2.0 project. The
full license text is in `LICENSE`. This directory is a local, self-contained
copy so the backend can import the engine without an external project
checkout; it is not a dependency on that project at runtime.

## Import model

Both packages use absolute imports rooted at their own names
(`from gptr... import ...`, `from multi_agents... import ...`). Importing the
`engine` package (this directory) inserts its own path into `sys.path` so
those resolve as top-level modules. Everything outside imports the engine via
`import engine` or through the adapters in `app.engine`.
