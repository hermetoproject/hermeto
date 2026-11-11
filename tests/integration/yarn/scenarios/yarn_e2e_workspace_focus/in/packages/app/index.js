import isNumber from "is-number";
import { isOdd, nestedMessage } from "shared";

console.log("isNumber(3):", isNumber(3));
console.log("isOdd(3):", isOdd(3));
if (nestedMessage !== "nested workspace works!") throw new Error("Missing nested workspace");
console.log("workspace focus works!");
