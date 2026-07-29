const colors = require('@colors/colors');
const boxen = require('boxen');
const camelcase = require('camelcase');
const fecha = require('fecha');
const isNumber = require('is-number');
const isPositive = require('is-positive');
const leftPad = require('rightpad');
const isBuffer = require('is-buffer');

console.log(colors.green('Hello world!'));

const currentDate = fecha.format(new Date(), 'MMMM Do YYYY');
console.log(`Today\'s date is ${currentDate}`);

const number = 2;
console.log(`${number} is a number: ${isNumber(number)}`);
console.log(`${number} is a positive number: ${isPositive(number)}`);

console.log(`${leftPad(number, 4)} <-- The number is now left-padded to four digits`);

console.log(`Is a Buffer: ${isBuffer(Buffer.from('hello'))}`);

console.log(boxen('Help, I\'m trapped inside this box!'));

const snakeCaseWord = "camel_case";
console.log(`What is ${snakeCaseWord} in camelCase: ${camelcase(snakeCaseWord)}`);
