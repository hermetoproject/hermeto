const yargs = require('yargs/yargs')
const { hideBin } = require('yargs/helpers')
const { format } = require('date-in-spanish')  // fecha.format
const { answerMeTheseQuestionsThree } = require('old-man-from-scene-24')
const holyHandGrenade = require('holy-hand-grenade')

const argv = yargs(hideBin(process.argv)).argv

const now = Date.now()

console.log(`Hello, ${argv.name ?? 'World'}!`)
console.log(`Today is ${format(now, 'dddd MMMM Do, YYYY')}`)

console.log()
for (let question of answerMeTheseQuestionsThree()) {
    console.log(question)
}

console.log()
console.log(holyHandGrenade)
