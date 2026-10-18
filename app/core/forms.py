"""Reading submitted forms with the types they really have."""
from starlette.datastructures import FormData


def field(form: FormData, key: str) -> str:
    """The text of a form field; '' when it is missing (or came as a file upload: a form value is a str or an UploadFile)."""
    value = form.get(key)
    return value if isinstance(value, str) else ''
