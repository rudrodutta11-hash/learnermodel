"""The cast — recurring characters whose stories the learner lives through.

A character is data: a persona plus an ordered arc of beats. Each beat is
one scene, and the arc builds so the learner slowly comes to care what
happens to them. The learning target (which words, which grammar) is NOT
here — that comes from the recommendation engine. These characters are the
container the learning hides inside.

Casts are subject-scoped and swappable: this Spanish-flavored cast is the
default, but any subject can register its own via `register_cast`. That is
what makes the Story Engine reusable — the engine knows nothing about
taxis or hotels, only about advancing a thread through an arc.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Beat:
    """One scene in a character's arc."""

    id: str
    situation: str   # the setup Kai stages
    hook: str        # the tension/emotion that makes the learner lean in
    reveal: str      # what this beat reveals — deepens investment
    memory: str      # what the learner will remember, for Kai to reference next time


@dataclass(frozen=True)
class Character:
    """A recurring character with a persona and a story arc."""

    id: str
    name: str
    role: str
    persona: str
    arc: tuple[Beat, ...]
    resting: str     # a low-stakes catch-up once their arc has played out

    @property
    def entrance(self) -> str:
        return self.arc[0].situation


CARLOS = Character(
    id="carlos",
    name="Carlos",
    role="taxi driver",
    persona=("Warm and talkative, drives with one hand and gestures with the "
             "other, treats every passenger like an old friend. A proud father "
             "who wears his heart on his sleeve."),
    arc=(
        Beat("carlos-1",
             "The learner flags down Carlos's taxi outside the airport. He "
             "greets them like he's been waiting all day for someone to talk to.",
             "He's saving every peso for his daughter Sofía's quinceañera and "
             "cannot stop talking about it.",
             "He shows a cracked-phone photo of Sofía in her dress.",
             "Carlos is a taxi driver saving for his daughter Sofía's quinceañera."),
        Beat("carlos-2",
             "Same taxi, another day. Carlos spots the learner in the mirror "
             "and lights up.",
             "The dress didn't fit, Sofía cried, and the seamstress wants more "
             "money he doesn't have.",
             "He quietly admits he's been driving double shifts to cover it.",
             "Sofía's dress didn't fit and money got tight — Carlos took on double shifts."),
        Beat("carlos-3",
             "Carlos is buzzing. The quinceañera is this Saturday.",
             "He nervously rehearses the father-daughter speech on the learner.",
             "He invites them to come to the party.",
             "The quinceañera is this Saturday; Carlos invited the learner and is nervous about his speech."),
        Beat("carlos-4",
             "Carlos picks the learner up, still glowing from the party.",
             "He wants to show every single photo and tell them Sofía cried "
             "happy tears.",
             "He thanks them for listening through the whole saga.",
             "The quinceañera was beautiful, Sofía cried happy tears, and Carlos thanked the learner for being there through it all."),
    ),
    resting="Carlos catches the learner up on Sofía and asks how they've been.",
)

MARIA = Character(
    id="maria",
    name="María",
    role="hotel receptionist",
    persona=("Crisp, unflappable, runs the front desk like a ship's captain. "
             "A dry sense of humor she only lets slip to people she likes."),
    arc=(
        Beat("maria-1",
             "The learner arrives to check in. María is polite, efficient, and "
             "clearly deep into a long shift.",
             "There's a snag with the reservation and she's untangling it "
             "without breaking a sweat.",
             "Almost to herself, she mentions she's never actually stayed in a hotel.",
             "María sorted out a reservation mix-up; she's never once stayed in a hotel herself."),
        Beat("maria-2",
             "María remembers the learner's name before they give it.",
             "She recommends a tiny dinner spot tourists never find.",
             "She's lived in this city her whole life and never left it.",
             "María recommended a hidden dinner spot; she's never left this city."),
        Beat("maria-3",
             "María seems distracted, a printed email tucked under the desk.",
             "She confesses she applied for a front-desk job at a hotel abroad.",
             "She asks the learner not to breathe a word to her manager.",
             "María secretly applied for a job abroad and asked the learner to keep it quiet."),
        Beat("maria-4",
             "María is waiting at the desk with news the moment the learner walks in.",
             "She got the interview — and she's terrified.",
             "She asks them to help her rehearse what she'll say.",
             "María landed the interview abroad and asked the learner to help her practice."),
    ),
    resting="María trades news about the job hunt between check-ins.",
)

ANA = Character(
    id="ana",
    name="Ana",
    role="coworker",
    persona=("Friendly and fast-talking, knows everyone's business, all cheer "
             "on the surface with real nerves underneath."),
    arc=(
        Beat("ana-1",
             "It's the learner's first day. Ana appoints herself their guide "
             "before they've even found their desk.",
             "She whisper-briefs who to trust and who to dodge by the coffee machine.",
             "She's covering for how nervous she is about a big presentation Friday.",
             "Ana showed the learner the ropes on day one; she's anxious about a big presentation Friday."),
        Beat("ana-2",
             "Ana corners the learner at lunch.",
             "The presentation got moved up and her slides are a disaster.",
             "She asks if they'll be her practice audience.",
             "Ana's presentation got moved up; the learner agreed to be her practice audience."),
        Beat("ana-3",
             "Ana is pacing by the learner's desk.",
             "She just presented, and her boss said something maddeningly ambiguous.",
             "Replaying every word, she admits how badly she wants the promotion.",
             "Ana presented; her boss was cryptic; she badly wants the promotion."),
        Beat("ana-4",
             "Ana practically vibrates over to the learner's desk.",
             "She got the promotion — and her first act is to thank them.",
             "She wants them on her new team.",
             "Ana got the promotion and wants the learner on her new team."),
    ),
    resting="Ana catches the learner up on office gossip and her new role.",
)

DIEGO = Character(
    id="diego",
    name="Diego",
    role="restaurant owner",
    persona=("Gruff and passionate, big-hearted under a brusque shell. Lives "
             "and breathes his late grandmother's recipes."),
    arc=(
        Beat("diego-1",
             "The learner wanders into a half-empty restaurant. Diego sizes "
             "them up, then decides to feed them properly.",
             "He insists they try the mole his abuela taught him to make.",
             "The place is struggling and he won't say it out loud.",
             "Diego fed the learner his abuela's mole; the restaurant is quietly struggling."),
        Beat("diego-2",
             "Diego remembers exactly what the learner ordered last time.",
             "A famous food critic is coming next week and he's pretending not to panic.",
             "He asks what they honestly thought of the food.",
             "A critic is coming; Diego asked for the learner's honest opinion of the food."),
        Beat("diego-3",
             "Diego is unusually quiet.",
             "The critic came, and the review runs tomorrow.",
             "He confesses the restaurant was his late abuela's dream, not just his.",
             "The restaurant was Diego's late abuela's dream; the review drops tomorrow."),
        Beat("diego-4",
             "Diego meets the learner at the door holding a newspaper.",
             "The review is out — and there's a line down the street.",
             "He names a dish on the menu after them.",
             "The review was glowing, there's a line down the street, and Diego named a dish after the learner."),
    ),
    resting="Diego insists on feeding the learner and catches them up on the restaurant.",
)

# Default Spanish cast. Order matters: it's the introduction order.
DEFAULT_CAST: tuple[Character, ...] = (CARLOS, MARIA, ANA, DIEGO)

_CASTS: dict[str, tuple[Character, ...]] = {}


def register_cast(subject: str, cast: tuple[Character, ...]) -> None:
    """Give a subject its own cast. Reusability lives here."""
    _CASTS[subject] = cast


def cast_for(subject: str) -> tuple[Character, ...]:
    return _CASTS.get(subject, DEFAULT_CAST)


def character_by_id(subject: str, character_id: str) -> Character | None:
    return next((c for c in cast_for(subject) if c.id == character_id), None)
