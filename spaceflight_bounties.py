"""Local question bank for the economy's spaceflight bounties."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SpaceflightBounty:
    question: str
    correct_answer: str
    wrong_answers: tuple[str, str, str]


def _bounty(question: str, correct: str, wrong_1: str, wrong_2: str, wrong_3: str) -> SpaceflightBounty:
    return SpaceflightBounty(question, correct, (wrong_1, wrong_2, wrong_3))


# Stable facts are kept locally so /bounty never depends on an external API.
_TRIVIA = (
  _bounty("Which Soviet spacecraft was the first to make a soft landing on the Moon?", "Luna 9", "Luna 2", "Luna 10", "Zond 3"),
    _bounty("Which spacecraft was the first to orbit another planet?", "Mariner 9", "Mariner 4", "Venera 9", "Pioneer 10"),
    _bounty("Which spacecraft was the first to enter orbit around Mercury?", "MESSENGER", "Mariner 10", "BepiColombo", "Pioneer 11"),
    _bounty("Which mission first successfully landed a spacecraft on Mars?", "Viking 1", "Mars 3", "Mariner 9", "Viking 2"),
    _bounty("Which spacecraft was the first to visit Uranus?", "Voyager 2", "Voyager 1", "Pioneer 11", "Galileo"),
    _bounty("Which spacecraft was the first to visit Neptune?", "Voyager 2", "Voyager 1", "Pioneer 10", "Cassini"),
    _bounty("Which Apollo mission carried the first Lunar Roving Vehicle?", "Apollo 15", "Apollo 14", "Apollo 16", "Apollo 17"),
    _bounty("Which Apollo mission was the final crewed mission to the Moon?", "Apollo 17", "Apollo 15", "Apollo 16", "Apollo 18"),
    _bounty("Which Gemini mission completed the first successful docking of two spacecraft in orbit?", "Gemini 8", "Gemini 6A", "Gemini 10", "Gemini 12"),
    _bounty("What spacecraft did Gemini 8 dock with?", "Agena Target Vehicle", "Apollo Command Module", "Centaur upper stage", "Surveyor 1"),
    _bounty("Which space station was the first to be placed in orbit?", "Salyut 1", "Skylab", "Mir", "Almaz 2"),
    _bounty("Which spacecraft became the first to land on Venus and transmit data from the surface?", "Venera 7", "Venera 4", "Venera 9", "Mariner 2"),
    _bounty("Which mission produced the first images from the surface of Venus?", "Venera 9", "Venera 7", "Venera 8", "Magellan"),
    _bounty("Which probe mapped most of Venus using synthetic-aperture radar?", "Magellan", "Galileo", "Mariner 10", "Venera 13"),
    _bounty("Which spacecraft first entered orbit around Jupiter?", "Galileo", "Voyager 1", "Pioneer 10", "Juno"),
    _bounty("Which spacecraft carried the atmospheric probe that entered Jupiter in 1995?", "Galileo", "Juno", "Voyager 2", "Cassini"),
    _bounty("Which Saturn moon has active water-rich plumes near its south pole?", "Enceladus", "Titan", "Iapetus", "Mimas"),
    _bounty("Which mission discovered strong evidence for a subsurface ocean on Europa through magnetic measurements?", "Galileo", "Voyager 2", "Juno", "Cassini"),
    _bounty("At which Sun-Earth Lagrange point does the James Webb Space Telescope operate?", "L2", "L1", "L4", "L5"),
    _bounty("What is the approximate altitude of a geostationary orbit above Earth's equator?", "35,786 km", "20,200 km", "42,164 km", "8,500 km"),
    _bounty("What orbital inclination is required for a perfectly geostationary satellite?", "0 degrees", "28.5 degrees", "51.6 degrees", "90 degrees"),
    _bounty("What is the name of the point in an orbit farthest from Earth?", "Apogee", "Perigee", "Apoapsis", "Perihelion"),
    _bounty("What is the name of the point in an orbit closest to the Sun?", "Perihelion", "Perigee", "Aphelion", "Periapsis"),
    _bounty("Which orbital maneuver transfers a spacecraft between two coplanar circular orbits using two burns?", "Hohmann transfer", "Bi-elliptic capture", "Gravity turn", "Plane-change transfer"),
    _bounty("In a Hohmann transfer from a lower circular orbit to a higher circular orbit, where is the second burn performed?", "At apoapsis", "At periapsis", "At the ascending node", "At the descending node"),
    _bounty("What does a spacecraft usually change with a burn normal to its orbital plane?", "Inclination", "Eccentricity only", "Orbital period only", "Periapsis altitude only"),
    _bounty("What rocket equation relates delta-v to exhaust velocity and mass ratio?", "Tsiolkovsky rocket equation", "Vis-viva equation", "Kepler equation", "Oberth equation"),
    _bounty("What effect makes a rocket burn more effective when performed at high orbital velocity?", "Oberth effect", "Kessler effect", "Yarkovsky effect", "Poynting-Robertson effect"),
    _bounty("Which equation gives orbital speed as a function of distance and semi-major axis?", "Vis-viva equation", "Tsiolkovsky equation", "Drake equation", "Hill equation"),
    _bounty("What is the region around a body where its gravity dominates the motion of satellites relative to another larger body called?", "Hill sphere", "Roche lobe", "Van Allen belt", "Magnetosphere"),
    _bounty("Which engine powered the first stage of the Saturn V?", "F-1", "J-2", "RL10", "H-1"),
    _bounty("Which engine powered the second and third stages of the Saturn V?", "J-2", "F-1", "RL10", "RS-25"),
    _bounty("What propellant combination did the Saturn V F-1 engine use?", "RP-1 and liquid oxygen", "Liquid hydrogen and liquid oxygen", "UDMH and nitrogen tetroxide", "Methane and liquid oxygen"),
    _bounty("What propellant combination did the Saturn V J-2 engine use?", "Liquid hydrogen and liquid oxygen", "RP-1 and liquid oxygen", "UDMH and nitrogen tetroxide", "Hydrazine and nitric acid"),
    _bounty("Which combustion cycle is used by the Space Shuttle Main Engine, later designated RS-25?", "Staged combustion", "Gas generator", "Expander cycle", "Pressure-fed"),
    _bounty("Which combustion cycle is used by the Merlin 1D engine?", "Gas generator", "Full-flow staged combustion", "Expander cycle", "Pressure-fed"),
    _bounty("Which engine uses a full-flow staged-combustion cycle?", "Raptor", "Merlin 1D", "F-1", "RL10"),
    _bounty("Which propellant combination is used by SpaceX Raptor engines?", "Methane and liquid oxygen", "RP-1 and liquid oxygen", "Liquid hydrogen and liquid oxygen", "Hydrazine and nitrogen tetroxide"),
    _bounty("What does RP-1 refer to?", "Highly refined kerosene rocket fuel", "A liquid hydrogen mixture", "A monopropellant hydrazine blend", "A solid propellant binder"),
    _bounty("Which engine family is strongly associated with the expander cycle?", "RL10", "F-1", "Merlin", "RD-180"),
    _bounty("Why is liquid hydrogen commonly used in high-performance upper stages?", "It provides very high specific impulse", "It has very high density", "It does not require insulation", "It is easy to store for years"),
    _bounty("Which property is a major disadvantage of liquid hydrogen as rocket fuel?", "Very low density", "Low specific impulse", "High freezing point", "It cannot be throttled"),
    _bounty("What does specific impulse primarily measure?", "Propellant efficiency", "Engine thrust only", "Fuel density", "Combustion chamber pressure"),
    _bounty("In what unit is specific impulse commonly expressed?", "Seconds", "Newtons", "Pascals", "Watts"),
    _bounty("What generally happens to rocket engine specific impulse in vacuum compared with sea level?", "It increases", "It decreases", "It becomes zero", "It is always unchanged"),
    _bounty("Why does a vacuum rocket nozzle usually have a larger expansion ratio?", "Ambient pressure is lower", "Vacuum engines need less thrust", "The propellant is colder", "Turbopumps cannot operate at sea level"),
    _bounty("What problem can occur if a highly expanded vacuum nozzle operates at sea level?", "Flow separation", "Cavitation in the fuel tank", "Combustion stops immediately", "The nozzle becomes magnetized"),
    _bounty("What is the primary purpose of a turbopump in a liquid rocket engine?", "Feed propellant into the chamber at high pressure", "Generate electrical power", "Steer the rocket", "Cool the payload"),
    _bounty("What normally drives the turbines in a gas-generator rocket engine?", "Hot gas from a separate preburner or gas generator", "Electric motors only", "Compressed helium from the payload", "Atmospheric air"),
    _bounty("What distinguishes a staged-combustion engine from a gas-generator engine?", "Turbine exhaust is fed into the main combustion chamber", "It has no turbopumps", "It only uses solid propellant", "It cannot throttle"),
    _bounty("What is special about a full-flow staged-combustion engine?", "All fuel and oxidizer pass through separate preburners before the main chamber", "It has no preburners", "It uses atmospheric oxygen", "It operates without turbopumps"),
    _bounty("Which Soviet-designed engine uses oxygen-rich staged combustion and powered the Atlas V first stage?", "RD-180", "NK-33", "RD-107", "RD-0120"),
    _bounty("The RD-180 is derived from which larger Soviet engine?", "RD-170", "RD-107", "NK-33", "RD-0120"),
    _bounty("Which engine powered the first stage of the Soviet N1 Moon rocket?", "NK-15", "RD-170", "RD-107", "RD-0120"),
    _bounty("How many engines were installed on the N1 first stage?", "30", "24", "16", "36"),
    _bounty("Which launch vehicle used a cluster of 27 Merlin engines on its first flight?", "Falcon Heavy", "Falcon 9", "Saturn V", "Delta IV Heavy"),
    _bounty("Which launch vehicle used three Common Booster Cores?", "Delta IV Heavy", "Falcon Heavy", "Atlas V", "Titan IV"),
    _bounty("What fuel did the Delta IV's RS-68 engine use?", "Liquid hydrogen", "RP-1", "Methane", "Hydrazine"),
    _bounty("Which engine powered the Space Shuttle Solid Rocket Boosters?", "They used solid propellant rather than a liquid engine", "RS-25", "RS-68", "AJ10"),
    _bounty("What oxidizer is commonly used with UDMH in hypergolic propulsion systems?", "Nitrogen tetroxide", "Liquid oxygen", "Hydrogen peroxide only", "Liquid fluorine"),
    _bounty("What does hypergolic mean?", "The propellants ignite on contact", "The propellant is cryogenic", "The engine uses electric ignition", "The fuel contains metallic powder"),
    _bounty("Why are hypergolic propellants useful for spacecraft maneuvering engines?", "They provide reliable repeated ignition", "They have the highest possible specific impulse", "They require no propellant tanks", "They cannot freeze"),
    _bounty("Which spacecraft engine type often uses hydrazine as a monopropellant?", "Reaction-control thruster", "Main cryogenic booster engine", "Solid rocket booster", "Ramjet"),
    _bounty("What catalyst is commonly used to decompose hydrazine in monopropellant thrusters?", "Iridium-based catalyst", "Copper wire", "Graphite powder", "Liquid oxygen"),
    _bounty("What is the main purpose of helium in many pressure-fed rocket propulsion systems?", "Pressurize the propellant tanks", "Act as the main fuel", "Ignite the propellant", "Cool the payload electronics"),
    _bounty("What is ullage in a rocket propellant tank?", "The gas-filled space above the liquid propellant", "The engine exhaust plume", "The nozzle throat", "The turbopump inlet"),
    _bounty("What is the purpose of an ullage motor on an upper stage?", "Settle propellant near the tank outlets before ignition", "Increase payload fairing pressure", "Spin the turbopump", "Separate the payload"),
    _bounty("What is pogo oscillation in a launch vehicle?", "vibration caused by coupling between propulsion and vehicle structure", "A roll-control maneuver", "A nozzle cooling method", "A type of stage separation"),
    _bounty("What is regenerative cooling in a rocket engine?", "Circulating propellant through passages around the chamber or nozzle", "Spraying water onto the outside of the rocket", "Using atmospheric air to cool the chamber", "Allowing the nozzle to melt slowly"),
    _bounty("What is film cooling in a rocket engine?", "A layer of relatively cool propellant protects the chamber or nozzle wall", "A ceramic film covers the entire rocket", "The engine is cooled by external air", "The nozzle is submerged in fuel"),
    _bounty("What does mixture ratio describe in a bipropellant rocket engine?", "The relative mass flow of oxidizer and fuel", "The number of engines per stage", "The ratio of thrust to vehicle mass", "The ratio of chamber pressure to atmospheric pressure"),
    _bounty("What is thrust-to-weight ratio for a rocket engine?", "Engine thrust divided by engine weight", "Vehicle mass divided by propellant mass", "Specific impulse divided by burn time", "Nozzle area divided by chamber area"),
    _bounty("What is the nozzle throat?", "The narrowest section of the nozzle", "The widest part of the nozzle exit", "The fuel injector inlet", "The turbopump exhaust"),
    _bounty("In a conventional rocket nozzle, where does the exhaust normally reach Mach 1?", "At the throat", "At the injector", "At the nozzle exit only", "Inside the propellant tank"),
    _bounty("What type of nozzle is used by most chemical rocket engines?", "De Laval nozzle", "Venturi intake", "Bellmouth intake", "Ramjet diffuser"),
    _bounty("What is engine gimbaling used for?", "Thrust vector control", "Propellant cooling", "Stage separation", "Payload deployment"),
    _bounty("Which launch vehicle famously used differential throttling rather than engine gimbaling for some control functions?", "N1", "Saturn V", "Atlas V", "Delta II"),
    _bounty("What is a common reason to use solid rocket motors on launch vehicles?", "High thrust and mechanical simplicity", "Very high throttle range", "Easy shutdown and restart", "Highest possible specific impulse"),
    _bounty("What is a major limitation of a conventional solid rocket motor after ignition?", "It is difficult or impossible to shut down", "It produces almost no thrust", "It cannot operate in vacuum", "It requires liquid oxygen"),
    _bounty("Which launch vehicle used the Vulcain engine on its core stage?", "Ariane 5", "Atlas V", "Proton", "Long March 2F"),
)


BOUNTIES = _TRIVIA


def _validate_question_bank() -> None:
    if len({bounty.question for bounty in BOUNTIES}) != len(BOUNTIES):
        raise RuntimeError("Spaceflight bounty questions must be unique")
    for bounty in BOUNTIES:
        answers = (bounty.correct_answer, *bounty.wrong_answers)
        if len(set(answers)) != 4:
            raise RuntimeError(f"Bounty answers must be unique: {bounty.question}")
        if any(len(answer) > 80 for answer in answers):
            raise RuntimeError(f"Bounty answer exceeds Discord's button-label limit: {bounty.question}")


_validate_question_bank()
