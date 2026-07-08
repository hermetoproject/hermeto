import lodash from 'lodash';

let numbers = [1, 2, 3, 4, 5];
let shuffledNumbers = lodash.shuffle(numbers);

console.log("Hello from second package!");

console.log('Original numbers:', numbers);
console.log('Shuffled numbers:', shuffledNumbers);
