package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io/ioutil"
)

type Config struct {
	Mappings map[string]string
}

var configContent = `
{
	"mappings": {
		"booking": "BOOKING_TABLE_NAME",
		"foobar": "BARFOO_TABLE_NAME"
	}
}

`

func main() {
	config := &Config{}
	if err := json.Unmarshal([]byte(configContent), config); err != nil {
		panic(err)
	}

	buffer := bytes.NewBuffer(nil)
	fmt.Fprint(buffer, `package foobar

	import "fmt"

	func Output() {
	`)
	for object, table := range config.Mappings {
		fmt.Fprintf(buffer, `fmt.Println("%s", "%s")`, object, table)
		fmt.Fprintf(buffer, "\n")
	}
	fmt.Fprintf(buffer, "\n}")
	if err := ioutil.WriteFile("foobar/foobar.go", buffer.Bytes(), 0644); err != nil {
		panic(err)
	}
}
