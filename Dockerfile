# A container that can answer "what tools does this server have?", and nothing
# more. It exists for directory listings that verify a server starts and
# responds to introspection.
#
# Read this before using it for anything else: **the tools inside cannot drive
# a device from here.** This server automates iOS through Xcode, simctl and
# WebDriverAgent, none of which exist on Linux, and a simulator cannot be
# reached from inside a container in any case. `ios_doctor` will say so, and
# every automation tool will fail the moment it is called.
#
# To actually drive a phone or a simulator, install it on a Mac with Xcode:
#
#     uvx ios-mcp                       # or: pip install ios-mcp
#     ./scripts/prepare_wda.sh simulator
#
# See https://github.com/emazaheri/ios-agent#readme
FROM python:3.12-slim

# Pinned rather than floating: an image whose behaviour changes under a
# directory's periodic re-check is worse than one that is explicitly stale.
RUN pip install --no-cache-dir ios-mcp==0.1.1

# Not root, since nothing here needs to be.
RUN useradd --create-home --uid 1000 app
USER app

# stdio transport, which is what an MCP client speaks to over the container's
# stdin and stdout. Logs go to stderr; stdout belongs to the protocol.
ENTRYPOINT ["ios-mcp", "serve"]
