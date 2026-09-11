# Some suggestions on how to structure your code to pass code review as fast as possible

_Note, that this text is supplementary and contains a condensation of
well-known best practices.  It is not mandatory to read it before contributing
(and likely is not necessary).  It is not intended to be read from the
beginning to the end, but rather  to be used as a reference and a
disambiguation tool for code reviewers._


## Table of contents

* [Background](#background)
* [Best practices](#best-practices)
  * [Document your decisions](#document-your-decisions)
  * [Write easy to follow code](#write-easy-to-follow-code)
  * [Favor styles which are easier to reason about](#favor-styles-which-are-easier-to-reason-about)
  * [Write clear tests which actually test functionality](#write-clear-tests-which-actually-test-functionality)
 * [FAQ](#faq)

## Background

Target audience for any piece of code is ultimately another engineer and not a
computer.  Thus sometimes a bit of code could be rejected by reviewers even if
it was greenlit by all linters and passed the single supplied unit test. This
usually happens because while potentially correct the code is hard to follow,
confusing or relies on problematic techniques. When submitting a contribution
to any open-source project it is worth keeping in mind that it has a potential
of living in the codebase for a long, sometimes very long time, and during this
time it might become necessary to examine or slightly modify it more than once.
It is very worth it to invest some effort into helping future maintainers by
writing clear and well documented (preferably self-documenting) code. This
small guide summarizes some best known practices and conventions which cannot
be caught with a linter.

> As an illustration to the above statement consider the following bit of code:
> ```
> &>:1-:v v *_$.@
>  ^    _$>\:^
> ```
> It is pretty hard to tell not just whether the code is correct, but even what is
> supposed to do (even when one knows the language). On the other hand
> ```
> factorial :: Integer -> Integer
> factorial n = product [1..n]
> ```
> is quite easy to comprehend without knowing much details of the underlying language.
> The ultimate goal is to write boring code that is as clear and straightforward as the
> second example.

The main goal of a reviewer is to make maintainers and future contributors
lives easier. Code reviews are done and requests for modifications are issued
not to flex or to cheaply show off superiority, but to ensure that the code
meets standards: is clear enough and well documented to be maintained, because
it is usually up to maintainers to deal with any bugs and inconsistencies in
contributed components.  All reviews are done in good faith and are
well-meaning, but prioritise long-term project stability.

Things that work in a individually maintained codebase do not work as well in a
collaborative project: code has to be clear enough for any engineer more or
less familiar with the codebase in general to modify it as fast as possible.
This requires a rather high level of input quality control, otherwise the
codebase would quickly devolve into a bug generator which is extremely
expensive to maintain in any semblance of working order.

Code author is usually the most knowledgeable subject matter expert when it
comes to specifics of workings and intent of their contribution. Procedures and
transformations which are trivial and obvious to them are very likely not
trivial and not obvious to everyone else. To ensure that the contributed code
remains used and useful it is important to clearly and succinctly share this
knowledge. Thus maintainers will likely ask to share all missing details or
even to restructure contributed code in such a way that it becomes clear what
it is doing.

All stated above is especially true for security-focused projects where bugs
and omissions could become very costly. The tolerances of a security project
are usually much tighter than of most others since the price of failure is much
greater.

## Best practices

### Document your decisions

 - Document your intent aka write good comments. Good comments are those which
   tell why something is done, not how.
   Do:
   ```python
   # A bug in foobar v2.0.1-v2.5.4 results in a silent failure to bazquux.
   # The check below is a workaround to prevent this.
   if did_fail(bazquux):
       ...
   ```
   Don't do:
   ```python
   x += 1  # Incrementing x.
   ```
 - Do not hesitate adding
   [doctests](https://docs.python.org/3/library/doctest.html) when it makes
   sense to do so!

 - When basing yor code off of some existing work please add a comment with a
   link to it.

 - When contributing regular expressions please provide comments. Either as
   [inline comments](https://docs.python.org/3/library/re.html#re.VERBOSE) or as
   [group names](https://docs.python.org/3/library/re.html#re.Match.groupdict).

### Write easy to follow code

 - Try avoiding extremely long lines in any direction. Horizontal lines longer
   than 120 symbols are as hard to deal with as a narrow 20+ lines columns of
   arguments to a single function.

 - Consider variable names which reflect intended use and potentially types of
   objects they will be bound with.

 - While doing the above remember, that vowel shortage is over, so in most
   cases it is ok to use vowels in variable names, a sligthly longer name won't
   be a problem and will help you readers a lot.

 - The opposite of a too-short name is a too-long name, so please try not
   packing everything known about an object into a name. Names longer than ~30 symbols
   are rather hard to deal with (at least for those who don't speak
   [German](https://en.wikipedia.org/w/index.php?title=Donaudampfschiffahrtselektrizit%C3%A4tenhauptbetriebswerkbauunterbeamtengesellschaft)
   natively).

 - Very short (even with just one symbol) names are fine in narrow scopes like
   `for` loops spanning a few lines or list comprehensions.
   ```[director_name(m) for m in most_popular_movies]```
   is a bit easier on a reader than
   ```[director_name(popular_movie) for popular_movie in most_popular_movies]```
   They are rarely a good idea elsewhere.

 - Consider using plural form of an individual entity name for naming
   homogeneous containers: `names` is a very well fitting name for an object of
   type `list[Name]`.

 - Respect continuity. If an argument is named `foo` in a definition of `bar`
   then it helps future reader if in a calling scope a variable that was to be
   passed to `bar` was also called `foo`.

 - Split your functions and methods. Helper functions defined either on module
   level or even dynamically within a code object that uses them will make the
   actual code so much easier to follow. Do this if:
   - Your function is clearly doing several things in sequence (each one
     deserves a helper);
   - Your function is longer than about 30 lines;
   - Your function has many levels of nesting;
   - You feel the need to write a brief comment explaining what the next code
     block is for;
   A good function is a boring sequence of statements with little to no
   branching which tells a story of simple transformations of arguments into
   output.

 - Avoid list comprehensions that span more than one line. This is usually a
   sign to either fall back to a for loop or to add some intermediate
   abstractions.

 - Aliases are cheap and can make code look much simpler when properly named.

 - Type aliases can make a huge difference for a reader:
   ```
   RawBarJSON = type(dict[str, Any])
   def foo(bars: list[RawBarJSON])
   ```
   vs
   ```
   def foo(bars: list[dict[str, Any]])
   ```
   Please use them. They also act as a form of documentation.

 - Strive for declarative code: what the code does is often of greater interest
   than how it does it.  Having a rich set of aliases and helper functions
   greatly helps with this.

 - Try to avoid reinventing the wheel. Python has a rich collection of built-in
   components with well-known behavior, which allows one to write concise and
   idiomatic code. It usually pays off to check if `collections`, `itertools` or
   `functools` contain something that could be reused.

 - When possible and practical implement
   [dunder methods](https://docs.python.org/3/reference/datamodel.html#basic-customization)
   for you classes.

 - When defining a base class consider making it abstract, this would
   disambiguate the intent. While there is some room for doubt with
   `NotImplementedError` (maybe it would be just fine if I have implemented it
   right here?) there is no doubt with `@abc.abstractmethod`.

### Favor styles which are easier to reason about

 - When possible prefer immutable objects, especially when defining containers
   with constants.  Use `frozenset` when there are no plans to extend it, use
   tuples instead of lists for static data.

 - When possible prefer building new, modified container objects from
   old ones to modifying existing container objects in place. A function that
   takes an argument and constructs a return value basing on it is easier to
   reason about than a function that modifies its argument.

 - Refresh recommendations from  [PEP8](https://peps.python.org/pep-0008)
   and [PEP20](https://peps.python.org/pep-0020);


### Write clear tests which actually test functionality

When writing tests remember, that tests look like code, but have a very
different nature.

 - Unlike for code it is ok for a test case to mostly repeat some other test.

 - Unlike in actual code there could be too much abstraction in tests. If
   understanding what and how exactly is being tested requires jumping through
   several modules chances are the test case is overly abstracted;

 - There is no limit on test names, it also makes a lot of sense to have
   longer, more descriptive names for test cases. Consider
   ```def test_foo()```
   vs 
   ```def test_foo_can_be_created_from_any_standard_source()```
   A failure report in the first case tells a user that a test has just failed,
   in the second case it also narrows down the scope of what could have gone
   wrong.

 - Prefer Arrange-Act-Assert pattern when possible.

 - Try not to overload individual assertion statements with constructor
   statements, especially with ones that span multiple lines. Remember, aliases
   are cheap!

 - Consider adding some meaningful message to `assert` statements.

 - Please avoid extending existing test cases when providing new functionality.
   If you find that some existing test is mostly fit for the task do not
   hesitate to copy it over, name it unambiguously and make necessary
   modifications to the code.

 - Adding a new test parameters group, on the other hand, is a great idea!

 - Consider adding a comment outlining what the test is about to do on higher
   level when the test is complex.


## FAQ

**Q** Is this guide exhaustive?

**A** No, but we are working on it. At the moment of writing it covers the most
   frequent patterns.

**Q** Is it correct in 100% of cases? Must this guide be obeyed blindly and
   without a thought?

**A** No. While this guide will likely be correct in most cases, always remember
   to use your best judgement. When breaking with these recommendations remember
   to follow this guide's suggestions on documenting your intent. The guide is a
   result of distillation of experience of multiple people across wide range of
   projects. The estimate is that the recommendations won't hold once in a rare
   while, but not too often. Frequent and far departures from the guide might
   indicate misunderstanding of its major points.

**Q** What about efficiency? Won't all these suggestions degrade efficiency of my code?

**A** Python is designed to be easy to write and comprehend, but not to be especially
   efficient. In other words it trades some CPU time for developer's time. While
   this does not justify the use of O(n^4) algorithm when an O(n) one suffices,
   it does justify introducing additional abstractions and intermediate objects
   to make code easier to comprehend and maintain. Furthermore, any discussion
   of efficiency must be based on data, either experimental or back-of-a-napkin
   estimate based on actual data about at least one of input dimensions.
   Another reasonably accurate and fast empirical way to answering a question
   "Does this need to be optimised?" could be obtained by instead answering "Do
   I want to reimplement my code as a C extension?". Sometimes more optimized
   code is desirable. In a case when optimization is warranted please do not
   forget extensively commenting the optimized code. (Especially the optimized
   code since it often tends to be more obscure.)

**Q** I have just found a counterexample to the guide in this same codebase! Hypocrisy!

**A** Thank you for reporting it! The codebase evolves and best practices
   evolve too, sometimes changes do not happen simultaneously. Please file
   either an issue to track the discrepancy or a PR to make the codebase more
   conformant, any will be greatly appreciated!

**Q** I disagree with this document, what should I do?

**A**  Please submit a PR! A _well-argumented_ change to best practices is
   a great contribution for which maintainers will be very grateful!
