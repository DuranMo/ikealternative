from pydantic import BaseModel, Field, ConfigDict, model_validator


class Light(BaseModel):
    id: str
    name: str
    room_id: str = 'unassigned'
    room: str = 'Unassigned'
    is_on: bool
    brightness: int | None = None
    reachable: bool | None = None
    dimmable: bool = True
    color_temperature: int | None = None
    color_hue: float | None = None
    color_saturation: float | None = None
    supports_color: bool = False
    supports_temperature: bool = False
    color_temperature_min: int | None = None
    color_temperature_max: int | None = None


class Room(BaseModel):
    id: str
    name: str
    light_ids: list[str]


class Scene(BaseModel):
    id: str
    name: str
    kind: str = 'instant'
    daily_time: str | None = None
    next_run: str | None = None


class Power(BaseModel):
    model_config = ConfigDict(extra='forbid')
    is_on: bool = Field(strict=True)


class Brightness(BaseModel):
    model_config = ConfigDict(extra='forbid')
    brightness: int = Field(ge=1, le=100, strict=True)


class SceneState(BaseModel):
    model_config = ConfigDict(extra='forbid')
    is_on: bool = Field(default=True, strict=True)
    brightness: int | None = Field(default=None, ge=1, le=100, strict=True)
    temperature: int | None = Field(default=None, ge=1000, le=10000, strict=True)
    hue: float | None = Field(default=None, ge=0, le=360)
    saturation: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode='after')
    def valid_color(self):
        if (self.hue is None) != (self.saturation is None):
            raise ValueError('Hue and saturation must be supplied together.')
        if self.temperature is not None and self.hue is not None:
            raise ValueError('Choose white temperature or RGB color, not both.')
        return self


class SceneFade(BaseModel):
    model_config = ConfigDict(extra='forbid')
    duration_seconds: int = Field(ge=1, le=86400, strict=True)
    start: SceneState


class SceneCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=80, pattern=r'\S')
    light_ids: list[str] = Field(min_length=1, max_length=100)
    state: SceneState | None = None
    fade: SceneFade | None = None
    daily_time: str | None = Field(default=None, pattern=r'^([01]\d|2[0-3]):[0-5]\d$')

    @model_validator(mode='after')
    def valid_fade(self):
        if self.fade is not None:
            if self.state is None:
                raise ValueError('A fade needs a target state.')
            start, end = self.fade.start, self.state
            if not start.is_on:
                raise ValueError('A fade starts with lights on; use low starting brightness for a sunrise.')
            if start.hue is not None or end.hue is not None:
                raise ValueError('Fades support brightness and white temperature; RGB colors support fixed scenes.')
            changed = False
            for field in ('brightness', 'temperature'):
                before, after = getattr(start, field), getattr(end, field)
                if (before is None) != (after is None):
                    raise ValueError('Provide both start and end values for each fade setting.')
                changed = changed or before != after
            if not changed:
                raise ValueError('A fade must change brightness or temperature.')
        return self


class SceneDetails(Scene):
    definition: SceneCreate | None = None
    editable: bool = True
    reason: str | None = None
    targets: list[dict] = Field(default_factory=list)


class ColorChange(BaseModel):
    model_config = ConfigDict(extra='forbid')
    temperature: int | None = Field(default=None, ge=1000, le=10000, strict=True)
    hue: float | None = Field(default=None, ge=0, le=360)
    saturation: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode='after')
    def valid_color(self):
        SceneState(temperature=self.temperature, hue=self.hue, saturation=self.saturation)
        if self.temperature is None and self.hue is None:
            raise ValueError('Supply a temperature or hue and saturation.')
        return self
