Potter
=============
Potter is a tool that creates containers. It aims to have all functionality of
`docker build`, but with several key improvments.

* Better caching controls to iterate faster when designing containers. Speed in general is a focus.
    * Specify a timeout in seconds afterwhich the layer will get rebuild
    * Specify a dependent set of files that trigger layer rebuild when changing
* Run multiple commands to create a single layer easily
* Standard file format. Potter config files can be YAML, JSON, or toml.
* Better cleanup. Old images from previous builds cleaned up automatically.
* Pluggable architecture. Custom Step objects can be imported dynamically at runtime.
