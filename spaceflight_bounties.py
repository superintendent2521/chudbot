"""Curated, stable spaceflight questions for economy bounties."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SpaceflightBounty:
    question: str
    correct_answer: str
    wrong_answers: tuple[str, str, str]


BOUNTIES = (
    SpaceflightBounty("Who was the first human in space?", "Yuri Gagarin", ("Alan Shepard", "John Glenn", "Neil Armstrong")),
    SpaceflightBounty("Which mission first landed humans on the Moon?", "Apollo 11", ("Apollo 8", "Apollo 10", "Apollo 13")),
    SpaceflightBounty("What was the first artificial satellite?", "Sputnik 1", ("Explorer 1", "Vanguard 1", "Luna 1")),
    SpaceflightBounty("Which planet is home to Olympus Mons?", "Mars", ("Venus", "Jupiter", "Mercury")),
    SpaceflightBounty("What does ISS stand for?", "International Space Station", ("Interstellar Science Ship", "International Satellite System", "Integrated Space Shuttle")),
    SpaceflightBounty("Which spacecraft carried the first humans to orbit the Moon?", "Apollo 8", ("Gemini 8", "Apollo 7", "Soyuz 4")),
    SpaceflightBounty("What was NASA's first reusable orbital spacecraft program?", "Space Shuttle", ("Gemini", "Mercury", "Skylab")),
    SpaceflightBounty("Which moon did the Huygens probe land on?", "Titan", ("Europa", "Ganymede", "Triton")),
    SpaceflightBounty("Which telescope launched aboard Space Shuttle Discovery in 1990?", "Hubble", ("Kepler", "Spitzer", "James Webb")),
    SpaceflightBounty("What is the boundary around a black hole called?", "Event horizon", ("Magnetopause", "Termination shock", "Roche limit")),
    SpaceflightBounty("Which country launched the first woman into space?", "Soviet Union", ("United States", "China", "France")),
    SpaceflightBounty("What launch vehicle sent Apollo astronauts toward the Moon?", "Saturn V", ("Atlas V", "Titan II", "Falcon Heavy")),
)
