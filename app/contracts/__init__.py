# Contracts — THE ONLY shared coupling surface between modules.
# Contains only typing.Protocol interfaces and Pydantic DTOs.
# No logic, no ORM models, no I/O.
# import-linter enforces that no module imports another module except through here.
