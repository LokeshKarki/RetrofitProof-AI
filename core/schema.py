"""Shared data model. Every other module imports from here.

Port conventions used everywhere (extraction prompt, graph, matching):
  valves    : ISO 5599 numbers  "1" supply, "2"/"4" working ports, "3"/"5" exhaust
  cylinders : "A" = cap side (extend), "B" = rod side (retract)
"""
from typing import Optional, Literal
from pydantic import BaseModel, Field

CompType = Literal[
    "cylinder", "valve", "service_unit", "sensor", "fitting", "other",
    # added after reading real competitor circuits (SMC supply / vacuum / manifold diagrams)
    "pressure_switch", "gauge", "regulator", "shutoff_valve", "shuttle_valve", "silencer",
    "manifold", "vacuum_ejector", "vacuum_pad", "accessory"]

SafetyStatus = Literal[
    "unknown",
    "potentially_safety_related",
    "engineer_confirmed_safety_related",
    "engineer_confirmed_not_safety_related",
]


class Component(BaseModel):
    id: str
    type: CompType
    label: Optional[str] = None
    manufacturer: Optional[str] = None
    part_number: Optional[str] = None
    callout_no: Optional[int] = None         # item number printed on the drawing / in its parts list
    qty: int = Field(default=1, ge=1)        # identical parts may be listed once with a quantity
    bore_mm: Optional[float] = None
    stroke_mm: Optional[float] = None
    pressure_bar: Optional[float] = None
    load_kg: Optional[float] = None          # cylinders: moved mass (technician input)
    stroke_time_s: Optional[float] = None    # cylinders: required time per stroke
    valve_function: Optional[str] = None     # e.g. "5/2 single solenoid spring return"
    voltage: Optional[str] = None            # e.g. "24VDC"
    safety_status: SafetyStatus = "unknown"
    confidence: float = Field(default=0.5, ge=0, le=1)


class Connection(BaseModel):
    from_component: str
    from_port: Optional[str] = None
    to_component: str
    to_port: Optional[str] = None
    connection_type: Literal["pneumatic", "signal"] = "pneumatic"
    confidence: float = Field(default=0.5, ge=0, le=1)


class IOPoint(BaseModel):
    tag: str
    direction: Literal["DI", "DO", "AI", "AO"]
    linked_component: Optional[str] = None
    description: Optional[str] = None
    plc_address: Optional[str] = None


class Requirements(BaseModel):
    supply_pressure_bar: Optional[float] = None
    cycles_per_hour: Optional[float] = None
    existing_controller: Optional[str] = None
    target_controller: Optional[str] = None


class Circuit(BaseModel):
    components: list[Component] = Field(default_factory=list)
    connections: list[Connection] = Field(default_factory=list)
    io: list[IOPoint] = Field(default_factory=list)
    requirements: Requirements = Field(default_factory=Requirements)
    unknowns: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
