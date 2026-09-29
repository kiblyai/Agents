# Northwind Analytics security overview

Northwind Analytics is a fictional B2B analytics company used to demonstrate the questionnaire agent.

## Compliance
We hold a SOC 2 Type II report covering security and availability. The most recent audit period ended on 30 June 2026. The report is available to customers under NDA.

## Hosting
Our production platform runs on Amazon Web Services in the us-east-1 region. We do not operate our own data centres.

## Encryption
Customer data is encrypted at rest with AES-256 using AWS KMS managed keys. All data in transit is encrypted with TLS 1.2 or higher.

## Access control
Multi-factor authentication is required for all employees through Okta single sign-on. Access to production follows role-based access control and is reviewed every quarter.

## Vulnerability management
An independent firm performs a penetration test once a year. Automated vulnerability scanning runs weekly on all production systems.

## People
All employees pass a background check before starting and complete security awareness training every year.

## Logging
Security logs are kept for one year and reviewed by the on-call engineer.
