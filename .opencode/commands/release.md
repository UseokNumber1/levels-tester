---

description: Create project release
agent: build
------------

Release type: $ARGUMENTS

Valid release types:

* patch
* minor
* major

Tasks:

1. Determine the current project version.

2. Validate the release type.
   If it is not patch, minor, or major, stop and ask the user.

3. Calculate the next version:

   * patch: X.Y.Z → X.Y.(Z+1)
   * minor: X.Y.Z → X.(Y+1).0
   * major: (X+1).0.0

4. Analyze all changes since the previous release:

   * git diff
   * recent commits
   * modified files

5. Generate a concise CHANGELOG entry describing:

   * added functionality
   * fixes
   * refactoring
   * configuration changes

6. Update:

   * VERSION
   * CHANGELOG.md
   * any other project version references if they exist

7. Run tests and validation commands appropriate for the project.

8. If any test or validation fails:

   * stop immediately
   * do not modify git history
   * do not create a tag
   * do not push

9. If all checks pass:

   git add .

   git commit -m "release: v<new_version>"

   git tag v<new_version>

   git push

   git push --tags

10. Show a release summary:

    * release type
    * previous version
    * new version
    * commit hash
    * created tag
    * changelog entry

Requirements:

* Never invent changes that are not present in the repository.
* Base changelog entries only on actual code changes.
* Do not skip tests.
* Ask for confirmation before performing git push and git push --tags.
