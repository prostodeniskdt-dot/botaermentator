# Repository workflow

- Work directly in `main`. It is the repository's single working branch.
- Do not create feature branches or pull requests unless the user explicitly changes this workflow.
- Before deleting a branch, verify that all its commits are reachable from `main`.
- Preserve user changes and existing credit, generation, continuation and delivery safeguards.
- Run checks appropriate to code changes. Documentation-only updates do not require repeating the
  full database suite when the implementation tree is unchanged.
- Do not deploy the application or change production Timeweb settings without the user's instruction.
- Keep Rich activation subject to the live-delivery/client checks required by the Editorial brief;
  merging the implementation alone does not change the deployment's environment variables.
