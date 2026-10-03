# Fractal Methodology: The 5 Horizons of Architectural Resolution

Rather than prescribing a rigid plan of specific files to edit, true refactoring requires an **n-step fractal methodology**. A fractal methodology means the exact same cognitive process is applied recursively at every level of scale. 

The process we apply is **PSBR**:
1. **Perceive**: Identify the coherent entities and the dissonance (entanglement, rot) between them.
2. **Sever**: Cut the entanglements. Define strict boundaries around the entities.
3. **Bridge**: Establish explicit, controlled contracts for how the severed entities communicate.
4. **Resolve**: Align the newly structured entities to satisfy the constraints of the horizon directly above it.

To apply this, we must descend through **5 Horizons of Abstraction**. At each horizon, we must understand the **Existentia**—the fundamental ontology of what "coheres" (holds together) at that specific scale.

---

### Horizon 0: Teleological (The System)
* **Existentia**: *The System-as-Purpose.* What coheres here is the ultimate goal (e.g., autonomous text-to-speech orchestration).
* **Perceive**: Recognize where the system fails its purpose (e.g., broken execution paths and internal friction slowing down the UX).
* **Sever**: Conceptually separate the fundamental capabilities (e.g., "The User Interface" vs. "The Background Brain").
* **Bridge**: Define the macro-operational flow (User triggers action $\rightarrow$ Brain processes $\rightarrow$ Audio plays).
* **Resolve**: Ensure the macro-architecture delivers the systemic goal without cognitive dissonance.

### Horizon 1: Architectural (The Domain)
* **Existentia**: *Bounded Contexts.* What coheres here are distinct business domains (e.g., Web API Routing, ML Inference, IPC Management). 
* **Perceive**: Identify domain blurring (God Objects like `web_server.py` handling both HTTP and TTS threading).
* **Sever**: Carve the monolith into isolated sub-domains. A domain must only change for one architectural reason.
* **Bridge**: Establish Domain APIs (Inter-Process Communication, shared caching layers, or strict boundaries).
* **Resolve**: Domains are now independent. They can be scaled, tested, or replaced without cascading failures into other domains.

### Horizon 2: Structural (The Component)
* **Existentia**: *Interfaces and Contracts.* What coheres here are the defined boundaries of objects and modules.
* **Perceive**: Identify tight coupling (e.g., high-level components depending directly on low-level concretions like an `mpv` subprocess).
* **Sever**: Extract abstractions. Hide implementation details behind interfaces (e.g., `IAudioController`).
* **Bridge**: Implement Dependency Inversion / Inversion of Control. Components are injected with their dependencies rather than constructing them.
* **Resolve**: Components become pluggable, modular, and strictly testable via mocks/stubs conforming to the contracts.

### Horizon 3: Behavioral (State & Concurrency)
* **Existentia**: *State Transitions and Data Flow.* What coheres here is the sequence of execution over time (Threads, Locks, Event Loops).
* **Perceive**: Uncover hidden, shared, or orphaned state (e.g., dead locks, ghost references to terminated processes).
* **Sever**: Isolate state ownership. A piece of state must be owned and mutated by exactly one actor or thread.
* **Bridge**: Replace shared mutable memory with immutable message passing (e.g., queues, pub-sub events).
* **Resolve**: Execution becomes deterministic. Race conditions, deadlocks, and temporal coupling are eliminated.

### Horizon 4: Syntactical (The Lexicon)
* **Existentia**: *The Abstract Syntax Tree (AST).* What coheres here are the literal tokens, keywords, and expressions of the language.
* **Perceive**: Identify semantic rot and lexical hallucinations (e.g., literal syntax errors, `if not False:`, empty `try: {}` blocks).
* **Sever**: Excise dead code, unused imports, and broken stubs without hesitation.
* **Bridge**: Write precise, type-safe syntactic replacements that strictly reflect the behavioral state (Horizon 3) and component contracts (Horizon 2).
* **Resolve**: The source code is mathematically reduced to its cleanest, most legible representation of reality.
