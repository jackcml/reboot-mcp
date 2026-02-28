from pydantic import BaseModel, Field


class CodeFunction(BaseModel):
    language: str = ""
    file_path: str = ""
    start_line: int = 0
    end_line: int = 0
    signature: str = ""
    docstring: str = ""
    confidence: float = 1.0


class CodeClass(BaseModel):
    language: str = ""
    file_path: str = ""
    start_line: int = 0
    end_line: int = 0
    methods: list[str] = Field(default_factory=list)
    docstring: str = ""
    confidence: float = 1.0


class CodeModule(BaseModel):
    language: str = ""
    file_path: str = ""
    imports: list[str] = Field(default_factory=list)
    confidence: float = 1.0
