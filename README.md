Potter
=============
Potter is a tool that creates containers. It aims to have all functionality of
`docker build`, but with several key improvments.

* Granular caching controls to iterate faster when designing containers. Speed in general is a focus.
* Standard file format. Potter config files can be YAML, JSON, or toml.
* Pluggable architecture. Custom Step objects can be imported dynamically at runtime.
