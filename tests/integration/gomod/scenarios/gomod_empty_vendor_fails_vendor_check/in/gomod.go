package integration_tests

import "rsc.io/quote"

func HelloFromDeps() string {
	return quote.Hello()
}
