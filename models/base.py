from bson import ObjectId
from pydantic import BaseModel, ConfigDict
from pydantic_core import core_schema

class PyObjectId(ObjectId):
    @classmethod
    def __get_validators__(cls):
        yield cls.validate

    @classmethod
    def validate(cls, v):
        if not ObjectId.is_valid(v):
            raise ValueError("Invalid objectid")
        return ObjectId(v)

    @classmethod
    def __get_pydantic_core_schema__(cls, _source_type, _handler):
        return core_schema.no_info_after_validator_function(
            cls.validate,
            core_schema.any_schema(),
            serialization=core_schema.to_string_ser_schema(),
        )

class BaseMongoModel(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, populate_by_name=True)